"""Model orchestrator engine: failover chain, per-model circuit breakers
(closed / open / half-open), refusal fallback and complexity routing."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.faults import OutageLLM, RefusingLLM  # noqa: E402
from finbot.llm import FakeLLM, make_engine  # noqa: E402
from finbot.models import ModelOrchestrator, ModelTarget, route_tier  # noqa: E402
from finbot.resilience import CircuitBreaker  # noqa: E402

DECLINED, APPROVED = "txn_A1B2C3D4", "txn_E5F6G7H8"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def settings(**kw):
    return Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent", data_dir="", **kw)


def bot_with(engine, **kw):
    b = build(settings(**kw))
    b.llm = engine
    b.sleep = b.toolbox.sleep = lambda s: None
    return b


def engine(*clients, fast=(), clock=None, threshold=3, cooldown=30):
    strong = [ModelTarget(n, c, attempts=2) for n, c in clients]
    fast_t = [ModelTarget(n, c, tier="fast", attempts=2) for n, c in fast]
    return ModelOrchestrator(strong, fast_t, sleep=lambda s: None, clock=clock or Clock(),
                             breaker_threshold=threshold, cooldown_s=cooldown)


class BreakerStates(unittest.TestCase):
    def test_closed_open_half_open_closed(self):
        c = Clock()
        br = CircuitBreaker("m", threshold=2, cooldown_s=10, clock=c)
        self.assertEqual(br.state, "closed")
        br.failure()
        br.failure()
        self.assertEqual(br.state, "open")
        self.assertFalse(br.allow())
        c.t = 10
        self.assertEqual(br.state, "half_open")
        self.assertTrue(br.available())   # checking does not consume the probe
        self.assertTrue(br.available())
        self.assertTrue(br.allow())       # one probe
        self.assertFalse(br.allow())      # second concurrent caller is rejected
        self.assertFalse(br.available())
        br.success()
        self.assertEqual(br.state, "closed")

    def test_failed_probe_doubles_cooldown_up_to_cap(self):
        c = Clock()
        br = CircuitBreaker("m", threshold=1, cooldown_s=10, max_cooldown_s=25, clock=c)
        br.failure()
        for expected in (20, 25, 25):
            c.t += br.cooldown
            self.assertTrue(br.allow())
            br.failure()
            self.assertEqual((br.state, br.cooldown), ("open", expected))


class Failover(unittest.TestCase):
    def test_primary_down_secondary_serves_and_breaker_skips_primary(self):
        clock = Clock()
        primary, backup = OutageLLM(FakeLLM(), down=True), FakeLLM()
        eng = engine(("bedrock-east", primary), ("bedrock-west", backup), clock=clock, threshold=2)
        b = bot_with(eng)
        cs = b.new_session("m_100", ["support"])
        t = b.ask(cs, f"why did {DECLINED} fail?")
        self.assertIn("declined", t.answer)
        self.assertEqual(set(t.models_used), {"bedrock-west"})
        self.assertEqual(eng.strong[0].breaker.state, "open")
        attempts = primary.attempts
        b.ask(cs, "what is code 51?")
        self.assertEqual(primary.attempts, attempts)       # open circuit: primary not even tried
        st = {s["model"]: s for s in eng.status()}
        self.assertGreater(st["bedrock-west"]["served_as_failover"], 0)

    def test_half_open_probe_returns_traffic_to_recovered_primary(self):
        clock = Clock()
        primary = OutageLLM(FakeLLM(), down=True)
        eng = engine(("primary", primary), ("backup", FakeLLM()), clock=clock, threshold=1, cooldown=30)
        b = bot_with(eng)
        cs = b.new_session("m_100", ["support"])
        b.ask(cs, f"why did {DECLINED} fail?")
        primary.down = False
        clock.t = 31                                        # cooldown over -> half-open probe
        t = b.ask(cs, f"status of {APPROVED}?")
        self.assertEqual(t.models_used[0], "primary")
        self.assertEqual(eng.strong[0].breaker.state, "closed")

    def test_mid_turn_failover_continues_same_turn(self):
        primary = OutageLLM(FakeLLM(), fail_after=1)       # serves the tool call, then dies
        eng = engine(("primary", primary), ("backup", FakeLLM()), threshold=1)
        b = bot_with(eng)
        t = b.ask(b.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        self.assertEqual(t.models_used, ["primary", "backup"])
        self.assertIn("declined", t.answer)
        self.assertIsNone(t.blocked_by)

    def test_all_models_down_is_a_clean_outage(self):
        eng = engine(("a", OutageLLM(FakeLLM(), down=True)), ("b", OutageLLM(FakeLLM(), down=True)))
        b = bot_with(eng)
        cs = b.new_session("m_100", ["support"])
        t = b.ask(cs, f"why did {DECLINED} fail?")
        self.assertEqual(t.blocked_by, "unavailable")
        self.assertEqual(cs.history, [])                   # rolled back; resend is safe

    def test_bad_request_is_not_failed_over(self):
        class BadRequest:
            def create(self, *a):
                raise ValueError("400: invalid parameter")   # config bug, not an outage
        backup = OutageLLM(FakeLLM())
        eng = engine(("primary", BadRequest()), ("backup", backup))
        with self.assertRaises(ValueError):
            eng.create("sys", [{"role": "user", "content": "hi"}], [])
        self.assertEqual(backup.attempts, 0)


class Refusals(unittest.TestCase):
    def test_refusal_falls_back_to_next_model(self):
        eng = engine(("strict", RefusingLLM()), ("backup", FakeLLM()))
        b = bot_with(eng)
        t = b.ask(b.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        self.assertIn("declined", t.answer)
        self.assertEqual(eng.status()[0]["refusals"], 2)    # both calls in the turn

    def test_refusal_on_last_model_is_reported(self):
        b = bot_with(engine(("only", RefusingLLM())))
        t = b.ask(b.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        self.assertEqual(t.answer, "I can't help with that request.")


class Routing(unittest.TestCase):
    def test_router_rules(self):
        self.assertEqual(route_tier("why did txn_A1B2C3D4 fail?", 1, 0, False), "fast")
        self.assertEqual(route_tier("compare txn_A and txn_B", 2, 0, False), "strong")
        self.assertEqual(route_tier("open a chargeback dispute", 0, 0, False), "strong")
        self.assertEqual(route_tier("simple question", 0, 1, False), "strong")   # earlier fallback
        self.assertEqual(route_tier("simple question", 0, 0, True), "strong")    # repair attempt

    def test_tiered_routing_uses_fast_for_simple_and_strong_for_hard(self):
        eng = engine(("opus", FakeLLM()), fast=[("sonnet", FakeLLM())])
        b = bot_with(eng, routing="tiered")
        cs = b.new_session("m_100", ["support"])
        simple = b.ask(cs, f"why did {DECLINED} fail?")
        hard = b.ask(cs, f"compare {DECLINED} and {APPROVED} for a dispute")
        self.assertEqual((simple.tier, set(simple.models_used)), ("fast", {"sonnet"}))
        self.assertEqual((hard.tier, set(hard.models_used)), ("strong", {"opus"}))

    def test_fast_tier_down_falls_back_to_strong(self):
        eng = engine(("opus", FakeLLM()), fast=[("sonnet", OutageLLM(FakeLLM(), down=True))])
        b = bot_with(eng, routing="tiered")
        t = b.ask(b.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        self.assertEqual(set(t.models_used), {"opus"})

    def test_single_routing_never_downgrades(self):
        eng = engine(("opus", FakeLLM()), fast=[("sonnet", FakeLLM())])
        b = bot_with(eng)  # routing defaults to "single"
        t = b.ask(b.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        self.assertEqual((t.tier, set(t.models_used)), (None, {"opus"}))


class Config(unittest.TestCase):
    def test_chain_and_fast_tier_from_settings(self):
        s = settings(model_chain="fake:primary@us-east-1, fake:secondary@us-west-2",
                     routing="tiered", fast_model="fake:quick")
        eng = make_engine(s)
        self.assertEqual([t.name for t in eng.strong], ["fake:primary@us-east-1", "fake:secondary@us-west-2"])
        self.assertEqual([t.name for t in eng.fast], ["fake:quick"])
        self.assertEqual([t.name for t in eng.chain_for("fast")],
                         ["fake:quick", "fake:primary@us-east-1", "fake:secondary@us-west-2"])

    def test_default_is_single_target(self):
        eng = make_engine(settings())
        self.assertEqual(len(eng.strong), 1)
        self.assertEqual(eng.fast, [])

    def test_status_shape(self):
        eng = engine(("a", FakeLLM()))
        eng.create("sys", [{"role": "user", "content": "what is code 51?"}], [])
        s = eng.status()[0]
        for key in ("model", "tier", "state", "calls", "ok", "failures", "refusals", "served_as_failover", "p50_ms"):
            self.assertIn(key, s)


if __name__ == "__main__":
    unittest.main()
