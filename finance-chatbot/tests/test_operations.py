"""Operations: context budget, session-store limits, tickets shared across workers,
crash re-runs, audit to stdout, and retry jitter."""
import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot import auditlog  # noqa: E402
from finbot.container import Container  # noqa: E402
from finbot.orchestrator import history_chars  # noqa: E402
from finbot.queue import InMemoryQueue, QueueConsumer  # noqa: E402
from finbot.store_dynamo import DynamoSessionStore, InMemoryDynamo, StateTooLarge  # noqa: E402
from finbot.tickets import DynamoTickets, open_ticket  # noqa: E402
from tests.test_layers import Clock, settings  # noqa: E402
from tests.test_tickets import APPROVED, NoModel, bot  # noqa: E402


class ContextBudget(unittest.TestCase):
    def test_history_size_counts_strings_dicts_and_blocks(self):
        class Block:
            text = "x" * 10
        history = [{"role": "user", "content": "abc"},
                   {"role": "assistant", "content": [Block(), {"type": "t", "c": "de"}]}]
        self.assertEqual(history_chars(history), 3 + 10 + 3)

    def test_thread_rolls_over_on_size_not_only_turn_count(self):
        c = Container(settings(max_history_chars=2_000))
        b = c.chatbot
        cs = b.new_session("m_100", ["support"])
        cs.history = [{"role": "user", "content": "x" * 5_000}]     # an earlier, very large exchange
        b.ask(cs, "how do refunds work?")
        self.assertLess(history_chars(cs.history), 2_000 + 5_000)
        self.assertNotIn("x" * 5_000, json.dumps(cs.history, default=str))
        self.assertEqual(cs.memory.turns_in_thread, 1)


class SessionStoreLimits(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = DynamoSessionStore(InMemoryDynamo(), clock=self.clock, idle_ttl_s=1800)
        self.store.create("s1", "m_100", {"x": 1})
        self.store.claim("s1", "w1", 60)

    def test_oversized_state_is_refused_clearly(self):
        self.store.begin_turn("s1", "m1", "w1", "hi")
        with self.assertRaisesRegex(StateTooLarge, "max_history_chars"):
            self.store.commit_turn("s1", "m1", "w1", 0, {"history": "x" * 400_000}, "a", {})

    def test_journal_entry_lives_as_long_as_the_session(self):
        self.store.begin_turn("s1", "m1", "w1", "hi")
        self.clock.t += 1_000
        self.store.claim("s1", "w1", 60)
        self.store.commit_turn("s1", "m1", "w1", 0, {"x": 2}, "a", {})
        turn = self.store.t.get("S#s1", "T#m1")
        self.assertEqual(turn["expires"], self.store.t.get("S#s1", "META")["hard_expires"])


class SharedTickets(unittest.TestCase):
    def test_two_workers_and_the_desk_see_the_same_tickets(self):
        table = InMemoryDynamo()
        a, b = DynamoTickets(table), DynamoTickets(table)
        kw = {"tenant_id": "m_100", "session_id": "s1", "role": "merchant", "kind": "refund", "txn_id": APPROVED,
              "text": "please refund", "facts": {"amount": "10.00"}}
        t1, created1 = open_ticket(a, **kw)
        t2, created2 = open_ticket(b, **kw)          # another pod, same request: one ticket
        self.assertEqual((t1.id, created1, created2), (t2.id, True, False))
        self.assertEqual([t.id for t in DynamoTickets(table).all()], [t1.id])

    def test_container_uses_dynamo_tickets_with_the_dynamo_store(self):
        c = Container(settings(store_provider="dynamodb"),
                      overrides={"store": DynamoSessionStore(InMemoryDynamo())})
        self.assertIsInstance(c.tickets, DynamoTickets)


class CrashRerun(unittest.TestCase):
    def test_rerun_after_a_crash_does_not_call_the_ticket_a_duplicate(self):
        c = bot(NoModel())
        b = c.chatbot
        cs = b.new_session("m_100", ["support"], "merchant")
        before = json.loads(json.dumps(cs.memory.tickets))
        first = b.ask(cs, f"please refund {APPROVED}")
        cs.memory.tickets = before                     # the commit never happened: memory rolls back
        rerun = b.ask(cs, f"please refund {APPROVED}")
        self.assertNotIn("already open", rerun.answer)
        self.assertEqual(first.reasons, rerun.reasons)
        self.assertEqual(len(c.tickets.all()), 1)
        again = b.ask(cs, f"please refund {APPROVED}")   # now the session knows it
        self.assertIn("already open", again.answer)


class AuditToStdout(unittest.TestCase):
    def test_turn_records_go_to_stdout_with_a_stream_field(self):
        out = io.StringIO()
        c = Container(settings(), audit_path="-")
        with contextlib.redirect_stdout(out):
            b = c.chatbot
            b.ask(b.new_session("m_100", ["support"]), "how do refunds work?")
        rec = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(rec["stream"], "turn")
        self.assertEqual(auditlog.sibling("-", ".control"), "-")
        self.assertEqual(auditlog.sibling("a.jsonl", ".control"), "a.jsonl.control")
        self.assertIsNone(auditlog.sibling(None, ".control"))


class RetryJitter(unittest.TestCase):
    def test_delays_are_spread_between_half_and_full(self):
        qc = QueueConsumer(InMemoryQueue(), worker=None)
        delays = {round(qc._jitter(30), 3) for _ in range(50)}
        self.assertTrue(all(15 <= d <= 30 for d in delays))
        self.assertGreater(len(delays), 10)


if __name__ == "__main__":
    unittest.main()
