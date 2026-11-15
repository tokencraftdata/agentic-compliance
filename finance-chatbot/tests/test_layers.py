"""Design layers: factories, security boundary, control plane, container, queue worker."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.config import Settings  # noqa: E402
from finbot.container import Container  # noqa: E402
from finbot.faults import OutageLLM  # noqa: E402
from finbot.llm import FakeLLM  # noqa: E402
from finbot.models import ModelOrchestrator, ModelTarget  # noqa: E402
from finbot.queue import InMemoryQueue, QueueConsumer  # noqa: E402
from finbot.registry import GUARDRAILS, LLM_PROVIDERS, Registry  # noqa: E402
from finbot.security import AuthError, Forbidden, HmacJwtVerifier  # noqa: E402
from finbot.service import BadRequest  # noqa: E402
from finbot.store import SessionStore  # noqa: E402
from finbot.store_dynamo import DynamoSessionStore, InMemoryDynamo  # noqa: E402
from finbot.verification import HallucinationGate  # noqa: E402

DECLINED = "txn_A1B2C3D4"
SECRET = "test-secret-that-is-at-least-32-characters-long"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def settings(**kw):
    base = {"llm_provider": "fake", "guardrail_provider": "local", "kb_path": "/nonexistent", "data_dir": "",
            "auth_mode": "hmac", "auth_secret": SECRET}
    return Settings(**{**base, **kw})


def container(clock, store=None, engine=None, **kw):
    over = {"store": store or SessionStore(clock=clock)}
    if engine:
        over["engine"] = engine
    c = Container(settings(**kw), overrides=over, clock=clock)
    c.chatbot.sleep = c.toolbox.sleep = lambda s: None
    return c


def two_model_engine(primary, backup):
    return ModelOrchestrator([ModelTarget("primary", primary, attempts=2), ModelTarget("backup", backup, attempts=2)],
                             sleep=lambda s: None, breaker_threshold=2, cooldown_s=30)


# --------------------------------------------------------------------- factories

class Factories(unittest.TestCase):
    def test_unknown_provider_names_the_options(self):
        with self.assertRaisesRegex(ValueError, "known: anthropic, bedrock, fake"):
            LLM_PROVIDERS.create("openai", model="x")

    def test_duplicate_registration_refused(self):
        r = Registry("thing")
        r.register("a")(lambda: 1)
        with self.assertRaises(ValueError):
            r.register("a")(lambda: 2)

    def test_settings_select_adapters(self):
        self.assertEqual(type(GUARDRAILS.create("local")).__name__, "LocalGuardrail")
        c = Container(settings(store_provider="sqlite", store_path=":memory:"))
        self.addCleanup(c.close)
        self.assertEqual(type(c.store).__name__, "SessionStore")


# --------------------------------------------------------------------- security

class Security(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.v = HmacJwtVerifier(SECRET, audience="finbot", clock=self.clock)

    def tok(self, **claims):
        return self.v.issue({"sub": "u1", "tenant_id": "m_100", "role": "merchant", "groups": [], **claims})

    def test_valid_token(self):
        p = self.v.verify(self.tok())
        self.assertEqual((p.tenant_id, p.role), ("m_100", "merchant"))

    def test_tampered_expired_alg_none_wrong_audience(self):
        head, payload, sig = self.tok().split(".")
        forged = self.v.issue({"sub": "u1", "tenant_id": "m_999", "role": "admin"}).split(".")[1]
        with self.assertRaises(AuthError):
            self.v.verify(f"{head}.{forged}.{sig}")
        with self.assertRaises(AuthError):
            self.v.verify(f"eyJhbGciOiJub25lIn0.{payload}.")
        expired = self.tok()
        self.clock.t += 3600
        with self.assertRaises(AuthError):
            self.v.verify(expired)
        other = HmacJwtVerifier(SECRET, audience="other-app", clock=self.clock)
        with self.assertRaises(AuthError):
            self.v.verify(other.issue({"sub": "u", "tenant_id": "m_100", "role": "merchant"}))

    def test_weak_secret_rejected(self):
        with self.assertRaises(ValueError):
            HmacJwtVerifier("short")


# --------------------------------------------------------------------- service boundary

class ServiceBoundary(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.c = container(self.clock)
        self.addCleanup(self.c.close)
        v = self.c.verifier
        self.merchant = v.issue({"sub": "u1", "tenant_id": "m_100", "role": "merchant"})
        self.other = v.issue({"sub": "u2", "tenant_id": "m_200", "role": "merchant"})
        self.admin = v.issue({"sub": "ops", "tenant_id": "internal", "role": "admin"})

    def test_chat_through_service(self):
        sid = self.c.service.start(self.merchant)
        r = self.c.service.send(self.merchant, sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertIn("declined", r["answer"])
        self.assertEqual(self.c.store.tenant_of(sid), "m_100")  # tenant from the token

    def test_other_tenant_and_unknown_session_look_the_same(self):
        sid = self.c.service.start(self.merchant)
        with self.assertRaisesRegex(Forbidden, "not found or not yours|not your session"):
            self.c.service.send(self.other, sid, "msg-0001", "hi")
        with self.assertRaisesRegex(Forbidden, "not found or not yours"):
            self.c.service.send(self.other, "no-such-session", "msg-0001", "hi")

    def test_admin_cannot_chat_and_merchant_cannot_control(self):
        with self.assertRaises(Forbidden):
            self.c.service.start(self.admin)
        with self.assertRaises(Forbidden):
            self.c.service.models_status(self.merchant)

    def test_input_validation(self):
        sid = self.c.service.start(self.merchant)
        with self.assertRaises(BadRequest):
            self.c.service.send(self.merchant, sid, "x", "hi")           # msg_id too short
        with self.assertRaises(BadRequest):
            self.c.service.send(self.merchant, sid, "msg-0001", "a" * 5000)
        with self.assertRaises(AuthError):
            self.c.service.send("garbage", sid, "msg-0001", "hi")


# --------------------------------------------------------------------- control plane

class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = DynamoSessionStore(InMemoryDynamo(), clock=self.clock)  # shared by two "processes"
        self.p1, self.p2 = FakeLLM(), FakeLLM()
        self.c1 = container(self.clock, self.store, two_model_engine(self.p1, FakeLLM()))
        self.c2 = container(self.clock, self.store, two_model_engine(self.p2, FakeLLM()))
        self.admin = self.c1.verifier.issue({"sub": "ops", "tenant_id": "internal", "role": "admin"})
        self.merchant = self.c1.verifier.issue({"sub": "u1", "tenant_id": "m_100", "role": "merchant"})

    def test_drain_applies_to_every_worker_and_is_audited(self):
        self.c1.service.drain_model(self.admin, "primary", "region maintenance")
        self.clock.t += 3  # past control_sync_s
        sid = self.c2.service.start(self.merchant)
        r = self.c2.service.send(self.merchant, sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertEqual(set(r["models"]), {"backup"})
        self.assertEqual(self.p2.calls, 0)
        self.assertEqual(self.c1.control.log[-1]["actor"], "ops")
        status = self.c2.service.models_status(self.admin)
        self.assertEqual(status["drained"], ["primary"])
        self.assertFalse(status["targets"][0]["enabled"])

        self.c1.service.restore_model(self.admin, "primary")
        self.clock.t += 3
        r = self.c2.service.send(self.merchant, sid, "msg-0002", "what is code 51?")
        self.assertEqual(r["models"][0], "primary")

    def test_cannot_drain_last_strong_model(self):
        self.c1.service.drain_model(self.admin, "primary", "x")
        with self.assertRaisesRegex(ValueError, "last enabled strong"):
            self.c1.service.drain_model(self.admin, "backup", "x")

    def test_tiered_routing_requires_a_fast_chain(self):
        with self.assertRaisesRegex(ValueError, "fast chain"):
            self.c1.service.set_routing(self.admin, "tiered", "cost")


# --------------------------------------------------------------------- hallucination gate

class Gate(unittest.TestCase):
    def test_repair_verified_against_its_own_tool_results(self):
        found = [{"name": "get_transaction", "input": {"txn_id": DECLINED},
                  "result": {"status": "found", "source": "txn_db",
                             "data": {"txn_id": DECLINED, "status": "declined", "amount": 10, "currency": "USD"}}}]
        gate = HallucinationGate(repair_attempts=1)
        calls = []
        r = gate.run(f"Transaction {DECLINED} was approved for 99.99 USD.", found, lambda: DECLINED,
                     lambda fb: (calls.append(fb) or f"Transaction {DECLINED} was declined for 10 USD.", found))
        self.assertEqual((r.action, r.repaired), ("allow", True))
        self.assertIn("<verifier>", calls[0])
        r = HallucinationGate(repair_attempts=0).run("It was approved for 99.99 USD.", found, lambda: DECLINED,
                                                     lambda fb: self.fail("no repair when disabled"))
        self.assertEqual(r.action, "block")


# --------------------------------------------------------------------- queue worker

class QueueWorker(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(clock=self.clock)
        self.addCleanup(self.store.close)
        self.q = InMemoryQueue(visibility_s=120, clock=self.clock)

    def consumer(self, engine=None):
        c = container(self.clock, self.store, engine)
        return c, QueueConsumer(self.q, c.worker, max_receives=3)

    def test_ack_on_commit_and_dedupe_on_redelivery(self):
        c, qc = self.consumer()
        sid = c.worker.start_session("m_100", ["support"])
        body = {"session_id": sid, "msg_id": "m1", "tenant_id": "m_100", "text": f"why did {DECLINED} fail?"}
        self.q.send(body)
        self.q.send(body)                          # duplicate delivery
        qc.poll_once()
        self.assertEqual((qc.stats.acked, len(self.q.items)), (2, 0))
        self.assertTrue(qc.stats.results[1]["deduplicated"])

    def test_outage_is_retried_later_then_answered(self):
        primary = OutageLLM(FakeLLM(), down=True)
        c, qc = self.consumer(ModelOrchestrator([ModelTarget("only", primary, attempts=1)],
                                                sleep=lambda s: None, breaker_threshold=5))
        sid = c.worker.start_session("m_100", ["support"])
        self.q.send({"session_id": sid, "msg_id": "m1", "tenant_id": "m_100", "text": f"why did {DECLINED} fail?"})
        qc.poll_once()
        self.assertEqual((qc.stats.retried, len(self.q.items)), (1, 1))
        primary.down = False
        self.clock.t += 31
        qc.poll_once()
        self.assertEqual((qc.stats.acked, len(self.q.items)), (1, 0))

    def test_forged_tenant_and_poison_messages_are_dead_lettered(self):
        c, qc = self.consumer()
        sid = c.worker.start_session("m_100", ["support"])
        self.q.send({"session_id": sid, "msg_id": "m1", "tenant_id": "m_999", "text": "hi"})
        qc.poll_once()
        self.assertEqual(self.q.dlq[0][1], "tenant mismatch")

        class Boom:
            def create(self, *a):
                raise ValueError("bug")              # non-transient: not failed over, raised
        c2, qc2 = self.consumer(ModelOrchestrator([ModelTarget("b", Boom())], sleep=lambda s: None))
        self.q.send({"session_id": sid, "msg_id": "m2", "tenant_id": "m_100", "text": "what is code 51?"})
        with self.assertLogs("finbot.queue", "ERROR"):   # logged with traceback, consumer stays up
            for _ in range(5):
                qc2.poll_once()
                self.clock.t += 400
        self.assertIn("failed 3 deliveries", self.q.dlq[-1][1])

    def test_dynamo_backed_end_to_end(self):
        store = DynamoSessionStore(InMemoryDynamo(), clock=self.clock)
        c = container(self.clock, store)
        qc = QueueConsumer(self.q, c.worker)
        sid = c.worker.start_session("m_100", ["support"])
        self.q.send({"session_id": sid, "msg_id": "m1", "tenant_id": "m_100", "text": f"why did {DECLINED} fail?"})
        qc.poll_once()
        self.assertEqual(store.load(sid)[0], 1)
        self.assertIn("declined", qc.stats.results[0]["answer"])


if __name__ == "__main__":
    unittest.main()
