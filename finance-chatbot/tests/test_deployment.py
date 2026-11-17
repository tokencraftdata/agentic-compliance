"""Deployment behaviour: the queue worker process under rollout, kill and redelivery."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.faults import CrashingLLM  # noqa: E402
from finbot.llm import FakeLLM  # noqa: E402
from finbot.models import ModelOrchestrator, ModelTarget  # noqa: E402
from finbot.queue import InMemoryQueue, Message, QueueConsumer, SqsQueue  # noqa: E402
from finbot.store import SessionStore  # noqa: E402
from finbot.worker_main import Runner, check_timeouts  # noqa: E402
from tests.test_layers import DECLINED, Clock, container  # noqa: E402


def engine(llm):
    return ModelOrchestrator([ModelTarget("only", llm, attempts=1)], sleep=lambda s: None, breaker_threshold=5)


class StopDuringTurn:
    """A model that triggers SIGTERM handling while the turn is in progress."""

    def __init__(self, base):
        self.base, self.runner = base, None

    def create(self, system, messages, tools):
        if self.runner:
            self.runner.stop()
        return self.base.create(system, messages, tools)


class WorkerProcess(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(clock=self.clock)
        self.addCleanup(self.store.close)
        self.q = InMemoryQueue(visibility_s=150, clock=self.clock)
        self.hb = os.path.join(tempfile.mkdtemp(), "hb")

    def pod(self, llm=None):
        c = container(self.clock, self.store, engine(llm) if llm else None)
        return c, Runner(lambda q: QueueConsumer(q, c.worker, max_receives=5), self.q, self.hb)

    def send(self, c, msg_id, sid=None):
        sid = sid or c.worker.start_session("m_100", ["support"])
        self.q.send({"session_id": sid, "msg_id": msg_id, "tenant_id": "m_100",
                     "text": f"why did {DECLINED} fail?"})
        return sid

    def test_one_message_per_poll_and_heartbeat(self):
        c, r = self.pod()
        self.send(c, "m1")
        self.send(c, "m2")
        r.run(max_polls=1)
        self.assertEqual((r.consumer.stats.acked, len(self.q.items)), (1, 1))
        self.assertTrue(os.path.exists(self.hb))

    def test_sigterm_finishes_the_turn_in_hand_and_leaves_the_rest_queued(self):
        model = StopDuringTurn(FakeLLM())
        c, r = self.pod(model)
        model.runner = r
        sid = self.send(c, "m1")
        self.send(c, "m2")
        polls = r.run()
        self.assertEqual(polls, 1)
        self.assertEqual(r.consumer.stats.acked, 1)          # the turn in hand was committed
        self.assertEqual(self.store.load(sid)[0], 1)
        self.assertEqual(len(self.q.items), 1)                # never received: another pod takes it

    def test_killed_pod_turn_is_rerun_once_by_another_pod(self):
        dead, r1 = self.pod(CrashingLLM(FakeLLM(), crash_on_call=1))
        sid = self.send(dead, "m1")
        with self.assertLogs("finbot.queue", "ERROR"):
            r1.run(max_polls=1)                               # dies mid-turn: nothing committed
        self.assertEqual(self.store.load(sid)[0], 0)
        self.assertEqual(len(self.q.items), 1)                # the message survives
        _, r2 = self.pod()
        self.clock.t += 61                                    # past the redelivery delay and the dead lease
        r2.run(max_polls=1)
        self.assertEqual(r2.consumer.stats.acked, 1)
        self.assertEqual(self.store.load(sid)[0], 1)          # committed exactly once
        self.assertEqual(len(self.q.items), 0)

    def test_visibility_must_exceed_turn_deadline(self):
        with self.assertRaises(SystemExit):
            check_timeouts(100, 90)
        check_timeouts(150, 90)


class FifoDeadLetter(unittest.TestCase):
    def test_fifo_dlq_gets_group_and_dedup_ids(self):
        sent = []

        class Sqs:
            def send_message(self, **kw):
                sent.append(kw)

            def delete_message(self, **kw):
                pass

        q = SqsQueue.__new__(SqsQueue)
        q.sqs, q.url, q.dlq_url, q.visibility_s = Sqs(), "main.fifo", "dlq.fifo", 150
        q.dead_letter(Message({"session_id": "s1", "msg_id": "m1"}, 6, "h"), "poison")
        self.assertEqual((sent[0]["MessageGroupId"], sent[0]["MessageDeduplicationId"]), ("s1", "dlq-m1"))


class DeploymentConfiguration(unittest.TestCase):
    def test_enabled_production_adapters_are_importable(self):
        # CI installs requirements.txt and checks these same imports inside the built image.
        from importlib import import_module
        for module in ("jwt", "opensearchpy", "gunicorn"):
            with self.subTest(module=module):
                import_module(module)

    def test_jwks_dynamo_requires_the_tenant_role_before_building_adapters(self):
        from finbot.config import Settings
        with self.assertRaisesRegex(ValueError, "FINBOT_TENANT_ROLE_ARN"):
            Settings(store_provider="dynamodb", auth_mode="jwks", tenant_role_arn="")
        Settings(store_provider="dynamodb", auth_mode="jwks", tenant_role_arn="arn:aws:iam::123:role/tenant")

    def test_reference_manifest_supplies_the_tenant_role(self):
        from pathlib import Path
        config = (Path(__file__).resolve().parents[1] / "deploy/k8s/config.yaml").read_text()
        self.assertIn("FINBOT_TENANT_ROLE_ARN: arn:aws:iam::ACCOUNT_ID:role/finbot-tenant-data", config)


class ReleaseExclusion(unittest.TestCase):
    def test_followup_cannot_enter_while_previous_turn_is_releasing(self):
        import threading

        from finbot.store import LeaseHeld

        c = container(Clock())
        self.addCleanup(c.close)
        sid = c.worker.start_session("m_100", ["support"])
        releasing, resume = threading.Event(), threading.Event()
        release = c.store.release
        errors = []

        def paused_release(*args):
            releasing.set()
            if not resume.wait(5):
                raise TimeoutError("test release was not resumed")
            release(*args)

        c.store.release = paused_release

        def first():
            try:
                c.worker.handle(sid, "msg-0001", f"why did {DECLINED} fail?")
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=first)
        thread.start()
        try:
            self.assertTrue(releasing.wait(5))
            with self.assertRaises(LeaseHeld):
                c.worker.handle(sid, "msg-0002", f"why did {DECLINED} fail?")
        finally:
            resume.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(c.worker.handle(sid, "msg-0002", f"why did {DECLINED} fail?")["version"], 2)


if __name__ == "__main__":
    unittest.main()
