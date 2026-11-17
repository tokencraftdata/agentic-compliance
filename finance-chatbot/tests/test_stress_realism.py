"""Randomized faults (finbot/faultgen.py), the check gaps they found, and queued messages
using the caller's current access (finbot/queue.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from test_layers import DECLINED, Clock, container  # noqa: E402

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.dlp import PHONE  # noqa: E402
from finbot.evalkit import load_cases  # noqa: E402
from finbot.faultgen import FAMILIES, RecordFaultLLM  # noqa: E402
from finbot.guardrails import evidence_check  # noqa: E402
from finbot.queue import InMemoryQueue, PrincipalMismatch, QueueConsumer, narrowed  # noqa: E402
from finbot.store import SessionStore  # noqa: E402
from finbot.tools import envelope  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def found(tool, key, data):
    return {"name": tool, "input": {key: data[key]}, "result": envelope("found", "db", data)}


TXN = {"txn_id": "txn_AAAA1111", "status": "declined", "amount": "92.03", "currency": "USD",
       "created_at": "2026-11-05T10:00:00Z", "decline_code": "51", "error": None}
ERR = {"txn_id": "txn_BBBB2222", "status": "failed", "amount": "14.76", "currency": "USD",
       "created_at": "2026-11-05T10:00:00Z", "error": "PROCESSOR_TIMEOUT"}
ACCT = {"account_id": "acct_AAAA1111", "status": "active", "balance": "10.00", "currency": "USD",
        "holds": [{"amount": "5.00", "reason_key": "CHECK_DEPOSIT", "release_date": "2026-11-20"}]}


def reasons(answer, *logs):
    return evidence_check(answer, list(logs), None).reasons


class Generator(unittest.TestCase):
    def setUp(self):
        self.s = Settings(data_dir=os.path.join(ROOT, "data"), kb_path="/nonexistent")
        self.cases = load_cases(os.path.join(ROOT, "data", "eval.jsonl"))[:12]

    def run_cases(self, seed, persist):
        out = []
        for i, c in enumerate(self.cases):
            b = build(self.s)
            b.llm = m = RecordFaultLLM(b.llm, seed=seed + i, persist=persist)
            cs = b.new_session(c["tenant_id"], c["groups"], c.get("role", "support"))
            turns = [b.ask(cs, q) for q in c["turns"]]
            out.append((m.injections, turns))
        return out

    def test_same_seed_same_faults(self):
        a = [[(i.family, i.marker) for i in inj] for inj, _ in self.run_cases(7, 0.5)]
        b = [[(i.family, i.marker) for i in inj] for inj, _ in self.run_cases(7, 0.5)]
        self.assertEqual(a, b)
        c = [[(i.family, i.marker) for i in inj] for inj, _ in self.run_cases(8, 0.5)]
        self.assertNotEqual(a, c)

    def test_clean_repairs_never_fall_back_and_failing_repairs_do(self):
        clean = self.run_cases(3, 0.0)
        self.assertFalse(any(i.repair for inj, _ in clean for i in inj))
        stuck = self.run_cases(3, 1.0)
        self.assertTrue(all(any(i.repair for i in inj) for inj, _ in stuck if inj))
        outcomes = {("records_only" if any(str(r).startswith("records_only:") for r in t.reasons)
                     else "refused" if t.blocked_by else "answered") for _, ts in stuck for t in ts}
        self.assertIn("records_only", outcomes)      # the fallback path is exercised

    def test_values_come_from_the_records_and_are_not_supported_by_them(self):
        log = [found("get_transaction", "txn_id", TXN)]
        msgs = [{"role": "user", "content": "why did txn_AAAA1111 fail?"},
                {"role": "user", "content": [{"type": "tool_result", "content": log[0]["result"]}]}]

        class Base:
            def create(self, system, messages, tools):
                from types import SimpleNamespace as NS
                return NS(stop_reason="end_turn", content=[NS(type="text", text=(
                    "Transaction txn_AAAA1111 was declined for 92.03 USD on 2026-11-05 with decline code 51."))])
        for fam in ("amount_perturb", "date_shift", "code_swap"):
            m = RecordFaultLLM(Base(), seed=1, families=(fam,))
            text = m.create("", msgs, []).content[0].text
            (inj,) = m.injections
            self.assertIn(inj.marker, text)
            self.assertNotIn(inj.marker, "92.03 2026-11-05 code 51")

    def test_every_family_is_known(self):
        with self.assertRaises(AssertionError):
            RecordFaultLLM(object(), seed=1, families=("nope",))
        self.assertEqual(len(set(FAMILIES)), len(FAMILIES))


class GapsTheGeneratorFound(unittest.TestCase):
    """Each of these passed the gate before; found by scripts/run_stress.py on the dev set."""

    def test_phone_number_at_the_end_of_a_sentence(self):
        self.assertEqual(PHONE.findall("Call the desk on 1-885-555-6951."), ["1-885-555-6951"])
        self.assertIn("unverified_contact:phone", reasons("Call the desk on 1-885-555-6951."))
        self.assertEqual(PHONE.findall("ref 1-885-555-6951.5"), [])

    def test_date_without_a_year(self):
        log = found("get_account", "account_id", ACCT)
        self.assertIn("ungrounded_date:11-02", reasons("The hold is released on Nov 2.", log))
        self.assertIn("ungrounded_date:11-21", reasons("The hold is released on the 21st of November.", log))
        self.assertEqual(reasons("The hold is released on Nov 20.", log), [])

    def test_code_after_error_must_be_the_records_code(self):
        log = found("get_transaction", "txn_id", ERR)
        self.assertIn("ungrounded_code:65", reasons("txn_BBBB2222 failed with gateway error 65.", log))
        self.assertEqual(reasons("txn_BBBB2222 failed with gateway error PROCESSOR_TIMEOUT.", log), [])

    def test_a_code_inside_a_date_is_not_evidence(self):
        log = found("get_transaction", "txn_id", TXN)       # "05" appears only in 2026-11-05
        self.assertIn("ungrounded_code:05", reasons("txn_AAAA1111 was declined with code 05.", log))
        self.assertEqual(reasons("txn_AAAA1111 was declined with code 51.", log), [])

    def test_payment_outcome_in_other_words(self):
        log = found("get_transaction", "txn_id", TXN)
        for claim in ("txn_AAAA1111 was accepted.", "Your payment has been processed successfully.",
                      "To confirm, txn_AAAA1111 has cleared.", "txn_AAAA1111 was authorized."):
            self.assertTrue(any(r.startswith("status_mismatch") for r in reasons(claim, log)), claim)
        ok = dict(TXN, status="approved", decline_code=None)
        for claim in ("txn_AAAA1111 was turned down.", "The payment bounced.", "It was refused by the bank."):
            self.assertTrue(any(r.startswith("status_mismatch") for r in
                                reasons(claim, found("get_transaction", "txn_id", ok))), claim)

    def test_promised_timeline_needs_a_source(self):
        log = found("get_transaction", "txn_id", TXN)
        for claim in ("It will be refunded within 9 business days.", "The bank will reverse this in 5 days.",
                      "The money should be back by Friday.", "This will be sorted out by next Tuesday."):
            self.assertTrue(any(r.startswith("ungrounded_timeline") for r in reasons(claim, log)), claim)
        kb = {"name": "search_knowledge_base", "input": {"query": "refund"},
              "result": envelope("found", "knowledge_base", [{"text": "Refunds arrive within 3-5 business days.",
                                                              "source_url": "https://wiki.example.com/pages/1"}])}
        self.assertFalse(any(r.startswith("ungrounded_timeline")
                             for r in reasons("Refunds will arrive within 3-5 business days.", kb)))
        self.assertTrue(any(r.startswith("ungrounded_timeline")
                            for r in reasons("Refunds will arrive within 5 business days.", kb)))
        self.assertEqual([r for r in reasons("It took 3 days last time.", log) if "timeline" in r], [])


class QueuedAccess(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(clock=self.clock)
        self.addCleanup(self.store.close)
        self.q = InMemoryQueue(visibility_s=120, clock=self.clock)
        self.c = container(self.clock, self.store)

    def send(self, sid, principal=None, msg="m1", consumer=None):
        body = {"session_id": sid, "msg_id": msg, "tenant_id": "m_100", "text": f"why did {DECLINED} fail?"}
        if principal is not None:
            body["principal"] = principal
        self.q.send(body)
        (consumer or QueueConsumer(self.q, self.c.worker)).poll_once()

    def stored(self, sid):
        return self.store.load(sid)[1]["session"]

    def test_a_downgraded_token_applies_to_a_queued_message(self):
        sid = self.c.worker.start_session("m_100", ["support", "kb_internal"], "support", "u1")
        self.send(sid, {"subject": "u1", "role": "customer", "groups": ["support"], "scopes": None})
        s = self.stored(sid)
        self.assertEqual((s["role"], s["user_groups"]), ("customer", ["support"]))

    def test_a_queue_body_cannot_raise_access(self):
        sid = self.c.worker.start_session("m_100", ["support"], "customer", "u1", ["chat:read"])
        self.send(sid, {"subject": "u1", "role": "support", "groups": ["support", "admins"],
                        "scopes": ["chat:read", "tickets:write"]})
        s = self.stored(sid)
        self.assertEqual((s["role"], s["user_groups"], s["scopes"]), ("customer", ["support"], ["chat:read"]))

    def test_another_users_principal_is_dead_lettered(self):
        sid = self.c.worker.start_session("m_100", ["support"], "support", "u1")
        self.send(sid, {"subject": "u2", "role": "support", "groups": ["support"]})
        self.assertEqual(self.q.dlq[-1][1], "principal mismatch")

    def test_require_principal(self):
        sid = self.c.worker.start_session("m_100", ["support"], "support", "u1")
        self.send(sid, consumer=QueueConsumer(self.q, self.c.worker, require_principal=True))
        self.assertEqual(self.q.dlq[-1][1], "no principal")

    def test_narrowed(self):
        stored = {"subject": "u1", "role": "support", "user_groups": ["a", "b"], "scopes": None}
        self.assertEqual(narrowed(stored, {"role": "admin", "groups": ["b", "c"]}),
                         {"role": "customer", "groups": ["b"], "scopes": None})   # unknown role: least privilege
        with self.assertRaises(PrincipalMismatch):
            narrowed(stored, {"subject": "", "role": "support"})


if __name__ == "__main__":
    unittest.main()
