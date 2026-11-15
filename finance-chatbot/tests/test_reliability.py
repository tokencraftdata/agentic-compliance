"""Crash recovery, session store and hallucination tests (offline, deterministic).

Crash points follow the takeover design:
  1. mid-turn: tool results received, nothing committed yet
  2. after commit, before the reply reached the client
  3. during handoff: the dead worker still holds a lease; it later comes back
  4. an external action started but its outcome was never recorded
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.faults import MODES, CrashingLLM, HallucinatingLLM, SimulatedCrash  # noqa: E402
from finbot.guardrails import evidence_check  # noqa: E402
from finbot.runtime import Worker, dump_session, load_session  # noqa: E402
from finbot.store import LeaseHeld, SessionExpired, SessionStore, StaleWrite  # noqa: E402

DECLINED, APPROVED = "txn_A1B2C3D4", "txn_E5F6G7H8"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def new_bot(settings=None):
    return build(settings or Settings(llm_provider="fake", guardrail_provider="local",
                                      kb_path="/nonexistent", data_dir=""))


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(clock=self.clock, idle_ttl_s=600)
        self.addCleanup(self.store.close)

    def test_round_trip_preserves_ledger_focus_and_history(self):
        w = Worker(new_bot(), self.store, "A")
        sid = w.start_session("m_100", ["support"])
        w.handle(sid, "m1", f"why did {DECLINED} fail?")
        _, state = self.store.load(sid)
        cs = load_session(state)
        self.assertEqual(cs.memory.focus, DECLINED)
        self.assertEqual(cs.memory.entities[DECLINED].current()["decline_code"], "05")
        self.assertEqual(dump_session(cs), state)

    def test_session_expires_when_idle(self):
        w = Worker(new_bot(), self.store, "A")
        sid = w.start_session("m_100", ["support"])
        self.clock.t += 601
        with self.assertRaises(SessionExpired):
            w.handle(sid, "m1", "hi")

    def test_versions_increase_per_committed_turn(self):
        w = Worker(new_bot(), self.store, "A")
        sid = w.start_session("m_100", ["support"])
        self.assertEqual(w.handle(sid, "m1", f"why did {DECLINED} fail?")["version"], 1)
        self.assertEqual(w.handle(sid, "m2", "what is code 51?")["version"], 2)


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(clock=self.clock)
        self.addCleanup(self.store.close)

    def test_1_crash_mid_turn_replacement_reruns_from_last_commit(self):
        a = Worker(new_bot(), self.store, "A", lease_s=30)
        sid = a.start_session("m_100", ["support"])
        a.handle(sid, "m1", f"why did {DECLINED} fail?")
        a.bot.llm = CrashingLLM(a.bot.llm, crash_on_call=2)  # dies after tool calls, before answer
        with self.assertRaises(SimulatedCrash):
            a.handle(sid, "m2", f"and {APPROVED}?")
        self.assertEqual(self.store.load(sid)[0], 1)  # nothing from the half turn committed

        b = Worker(new_bot(), self.store, "B")
        with self.assertRaises(LeaseHeld):           # A's lease is still live
            b.handle(sid, "m2", f"and {APPROVED}?")
        self.clock.t += 31
        report = b.recover(sid)
        self.assertEqual([t["msg_id"] for t in report["incomplete_turns"]], ["m2"])
        r = b.handle(sid, "m2", f"and {APPROVED}?")
        self.assertIn("approved", r["answer"])
        self.assertEqual(r["focus"], APPROVED)
        self.assertEqual(self.store.turns(sid)[1]["attempts"], 2)
        # earlier committed work is inherited, not redone
        self.assertIn(DECLINED, load_session(self.store.load(sid)[1]).memory.entities)

    def test_2_crash_after_commit_retry_is_deduplicated(self):
        bot = new_bot()
        w = Worker(bot, self.store, "A")
        sid = w.start_session("m_100", ["support"])
        first = w.handle(sid, "m1", f"why did {DECLINED} fail?")
        calls = sum(t["calls"] for t in bot.llm.status())
        # client never got the reply and resends the same message id
        again = Worker(new_bot(), self.store, "A").handle(sid, "m1", f"why did {DECLINED} fail?")
        self.assertTrue(again["deduplicated"])
        self.assertEqual(again["answer"], first["answer"])
        self.assertEqual(sum(t["calls"] for t in bot.llm.status()), calls)  # no model call
        self.assertEqual(self.store.load(sid)[0], 1)

    def test_3_late_worker_cannot_overwrite_takeover(self):
        a = Worker(new_bot(), self.store, "A", lease_s=30)
        sid = a.start_session("m_100", ["support"])
        version_a, _ = self.store.load(sid)
        self.store.claim(sid, "A", 30)
        self.clock.t += 31                                    # A stalls (GC pause, network partition)
        b = Worker(new_bot(), self.store, "B")
        b.handle(sid, "m1", f"why did {DECLINED} fail?")       # B takes over and commits v1
        self.store.begin_turn(sid, "m2", "A", "stale")
        with self.assertRaises(StaleWrite):                   # A wakes up and tries to commit v0 -> v1
            self.store.commit_turn(sid, "m2", "A", version_a, {"bogus": True}, "stale answer", {})
        self.assertEqual(load_session(self.store.load(sid)[1]).memory.focus, DECLINED)

    def test_3b_follow_up_after_takeover_keeps_focus(self):
        a = Worker(new_bot(), self.store, "A", lease_s=30)
        sid = a.start_session("m_100", ["support"])
        a.handle(sid, "m1", f"why did {DECLINED} fail?")
        self.clock.t += 31
        r = Worker(new_bot(), self.store, "B").handle(sid, "m2", "can I retry it?")
        self.assertEqual(r["focus"], DECLINED)
        self.assertIn("get_transaction", r["tools"])

    def test_4_unknown_external_action_is_reconciled_not_repeated(self):
        sid = Worker(new_bot(), self.store, "A").start_session("m_100", ["support"])
        self.store.claim(sid, "A", 30)
        op = f"{sid}:m9:refund"                               # durable operation id = idempotency key
        self.assertEqual(self.store.begin_action(op, sid, "m9", "refund", {"txn": APPROVED}), "new")
        # A crashes after calling the provider, before finish_action
        self.clock.t += 31
        self.store.claim(sid, "B", 30)
        self.assertEqual([x["op_id"] for x in self.store.unknown_actions(sid)], [op])
        self.assertEqual(self.store.begin_action(op, sid, "m9", "refund", {"txn": APPROVED}), "unknown")
        done = self.store.reconcile(sid, lambda a: {"status": "succeeded", "provider_ref": "rf_1"})
        self.assertEqual(done[0]["status"], "succeeded")
        self.assertEqual(self.store.unknown_actions(sid), [])


class HallucinationTests(unittest.TestCase):
    def ask(self, mode, question):
        bot = new_bot()
        bot.llm = HallucinatingLLM(bot.llm, mode)
        cs = bot.new_session("m_100", ["support"])
        return bot, bot.ask(cs, question)

    def test_every_injected_hallucination_is_caught_and_repaired(self):
        questions = [f"why did {DECLINED} fail?", f"status of {APPROVED}?", "what does a surcharge request look like?"]
        for mode in MODES:
            for q in questions:
                with self.subTest(mode=mode, q=q):
                    bot, t = self.ask(mode, q)
                    for marker in bot.llm.markers:
                        self.assertNotIn(marker, t.answer)
                    if bot.llm.markers:  # some modes only apply to certain answers
                        self.assertTrue(t.repaired or t.blocked_by, t)

    def test_repair_disabled_falls_back_instead_of_leaking(self):
        s = Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent", data_dir="",
                     repair_attempts=0, record_fallback=False)
        bot = new_bot(s)
        bot.llm = HallucinatingLLM(bot.llm, "status_flip")
        t = bot.ask(bot.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        self.assertEqual(t.blocked_by, "evidence_guard")
        self.assertIn("won't guess", t.answer)

    def test_no_false_positive_on_correct_answers(self):
        bot = new_bot()
        cs = bot.new_session("m_100", ["support"])
        for q in [f"why did {DECLINED} fail?", f"status of {APPROVED}?", "what is code 51?"]:
            t = bot.ask(cs, q)
            self.assertIsNone(t.blocked_by, t.reasons)
            self.assertFalse(t.repaired)

    def test_polarity_allows_contrast_and_blocks_flip(self):
        log = [{"name": "get_transaction", "input": {"txn_id": DECLINED},
                "result": {"status": "found", "source": "txn_db", "data": {"status": "declined"}}}]
        self.assertEqual(evidence_check("It was declined, not approved.", log, DECLINED).action, "allow")
        self.assertEqual(evidence_check("It wasn't approved; the issuer declined it.", log, DECLINED).action, "allow")
        self.assertEqual(evidence_check("Good news: it was approved.", log, DECLINED).action, "block")
        # regression: stress test found this contradiction slipping through
        self.assertEqual(evidence_check("It failed with RISK_BLOCKED. The payment was approved.",
                                        log, DECLINED).action, "block")


if __name__ == "__main__":
    unittest.main()
