"""Claim binding: with several records in a turn, each stated value must come from the record
its clause is about (finbot/binding.py), and a correct answer about several records passes.

Before binding, every case under SwapsBlocked passed the gate: each amount, code and status was
real, just attached to the wrong record. Every case under CorrectAnswersPass was blocked for the
domain records, because each sentence was checked against every record.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.binding import binding_check, segments  # noqa: E402
from finbot.domains.checks import domain_check  # noqa: E402
from finbot.guardrails import evidence_check  # noqa: E402
from finbot.verification import BindingVerifier, DomainVerifier, EvidenceVerifier, VerifierChain  # noqa: E402

A = {"txn_id": "txn_AAAA1111", "status": "approved", "amount": "28.24", "currency": "USD",
     "created_at": "2026-11-01T10:00:00Z", "decline_code": None}
B = {"txn_id": "txn_BBBB2222", "status": "declined", "amount": "12.66", "currency": "USD",
     "created_at": "2026-11-03T09:00:00Z", "decline_code": "N7"}
C = {"txn_id": "txn_CCCC3333", "status": "failed", "amount": "40.10", "currency": "USD",
     "created_at": "2026-11-04T09:00:00Z", "decline_code": None}
L1 = {"loan_id": "loan_AAAA1111", "status": "declined", "principal": "53714.68", "currency": "USD"}
L2 = {"loan_id": "loan_BBBB2222", "status": "paid_off", "principal": "71651.11", "currency": "USD"}
T1 = {"transfer_id": "xfer_AAAA1111", "status": "completed", "amount": "1482.22", "currency": "USD"}
T2 = {"transfer_id": "xfer_BBBB2222", "status": "returned", "amount": "1496.28", "currency": "USD",
      "return_code": "R01"}


def call(tool, key, rec):
    return {"name": tool, "input": {key: rec[key]}, "result": {"status": "found", "source": "db", "data": rec}}


PAY = [call("get_transaction", "txn_id", A), call("get_transaction", "txn_id", B)]
LOANS = [call("get_loan", "loan_id", L1), call("get_loan", "loan_id", L2)]
XFERS = [call("get_transfer", "transfer_id", T1), call("get_transfer", "transfer_id", T2)]
GATE = VerifierChain([EvidenceVerifier(), DomainVerifier(), BindingVerifier()])


class SwapsBlocked(unittest.TestCase):
    def blocked(self, answer, log):
        r = GATE.check(answer, log, None)
        self.assertEqual(r.action, "block", answer)
        return r.reasons

    def test_payment_amounts_swapped(self):
        reasons = self.blocked("txn_AAAA1111 for 12.66 USD was approved. txn_BBBB2222 for 28.24 USD was declined.", PAY)
        self.assertIn("value_from_other_record:amount:txn_AAAA1111<-txn_BBBB2222", reasons)

    def test_payment_statuses_swapped(self):
        reasons = self.blocked("txn_AAAA1111 for 28.24 USD was declined. txn_BBBB2222 for 12.66 USD was approved.", PAY)
        self.assertIn("status_mismatch:txn_AAAA1111:record=approved", reasons)

    def test_code_moved_to_the_other_payment(self):
        self.assertIn("value_from_other_record:code:txn_AAAA1111<-txn_BBBB2222",
                      self.blocked("txn_AAAA1111 has decline code N7.", PAY))

    def test_date_moved_to_the_other_payment(self):
        self.assertIn("value_from_other_record:date:txn_AAAA1111<-txn_BBBB2222",
                      self.blocked("txn_AAAA1111 was approved on 2026-11-03.", PAY))

    def test_declined_and_failed_are_different_facts(self):
        log = [call("get_transaction", "txn_id", B), call("get_transaction", "txn_id", C)]
        self.blocked("txn_BBBB2222 failed. txn_CCCC3333 was declined.", log)

    def test_one_sentence_two_records(self):
        self.blocked("txn_AAAA1111 was declined and txn_BBBB2222 was approved.", PAY)

    def test_follow_on_sentence_is_about_the_record_before_it(self):
        self.blocked("Let's look at txn_AAAA1111. It was declined. txn_BBBB2222 was declined too.", PAY)

    def test_loan_amounts_swapped(self):
        self.blocked("loan_AAAA1111 is declined, for 71651.11 USD. loan_BBBB2222 is paid off, for 53714.68 USD.",
                     LOANS)

    def test_loan_statuses_swapped(self):
        self.blocked("loan_AAAA1111 is paid off. loan_BBBB2222 is declined.", LOANS)

    def test_transfer_return_code_moved(self):
        self.assertIn("value_from_other_record:code:xfer_AAAA1111<-xfer_BBBB2222",
                      self.blocked("xfer_AAAA1111 came back with R01.", XFERS))


class CorrectAnswersPass(unittest.TestCase):
    def allowed(self, answer, log):
        r = GATE.check(answer, log, None)
        self.assertEqual(r.action, "allow", (answer, r.reasons))

    def test_two_payments(self):
        self.allowed("txn_AAAA1111 for 28.24 USD was approved. txn_BBBB2222 for 12.66 USD was declined "
                     "with code N7.", PAY)

    def test_two_payments_one_sentence(self):
        self.allowed("txn_AAAA1111 was approved, but txn_BBBB2222 was declined.", PAY)

    def test_two_loans(self):
        self.allowed("loan_AAAA1111 is declined, for 53714.68 USD. loan_BBBB2222 is paid off, for 71651.11 USD.",
                     LOANS)

    def test_two_transfers(self):
        self.allowed("xfer_AAAA1111 is completed, for 1482.22 USD. xfer_BBBB2222 is returned, code R01.", XFERS)

    def test_follow_on_sentence(self):
        self.allowed("Let's look at txn_BBBB2222. It was declined with code N7.", PAY)

    def test_shared_help_page_value_may_appear_anywhere(self):
        kb = {"name": "search_knowledge_base", "input": {"query": "fees"},
              "result": {"status": "found", "source": "kb", "data": [{"text": "A 12.66 USD review fee applies."}]}}
        self.assertEqual(binding_check("txn_AAAA1111 was approved; a 12.66 USD fee can apply.", PAY + [kb]), [])


class Scope(unittest.TestCase):
    def test_logs_fetched_before_the_record(self):
        logs = {"name": "search_transaction_logs", "input": {"txn_id": "txn_AAAA1111"},
                "result": {"status": "found", "source": "logs", "data": [{"event": "auth"}]}}
        self.assertTrue(binding_check("txn_AAAA1111 was declined.", [logs] + PAY))

    def test_single_record_is_unchanged(self):
        self.assertEqual(binding_check("txn_AAAA1111 for 99.99 USD was declined.", PAY[:1]), [])

    def test_the_other_one_is_not_inherited(self):
        clauses = segments("txn_AAAA1111 was approved. The other one was declined.", {"txn_AAAA1111", "txn_BBBB2222"})
        self.assertEqual(clauses[1][1], None)

    def test_unbound_clause_is_still_checked_against_every_domain_record(self):
        # "It" with no record named before it: the domain check stays conservative.
        self.assertTrue(domain_check("It is paid off.", LOANS))

    def test_evidence_check_alone_misses_the_swap(self):
        # Why binding is a separate verifier: per-value grounding sees every value in the turn.
        r = evidence_check("txn_AAAA1111 for 12.66 USD was approved. txn_BBBB2222 for 28.24 USD was declined.",
                           PAY, None)
        self.assertEqual(r.action, "allow")


if __name__ == "__main__":
    unittest.main()
