"""The domain plug-ins: accounts, transfers, KYC, lending, issuing, onboarding.

What must hold, whatever the model does:
  * a record's hidden fields (a review reason, a credit score) never reach the model;
  * a wrong current status, cause or reason key in an answer is blocked;
  * "cancel / unblock / extend" on a domain record becomes a ticket, never an action;
  * ids the user never typed cannot be fetched (provenance), and other tenants' records
    don't exist;
  * when the model's answer fails, the record-built answer states only validated fields;
  * FINBOT_DOMAINS decides which tools exist (payments can be switched off).
"""
from __future__ import annotations

import unittest

from finbot.config import Settings
from finbot.container import Container
from finbot.domains import DOMAINS, by_id
from finbot.domains.checks import domain_check
from finbot.recordanswer import record_answer
from finbot.tools import DEMO_RECORDS

ACCT = "acct_M4N5P6Q7"
XFER = "xfer_R8S9T0U1"
KYC = "kyc_V2W3X4Y5"
LOAN = "loan_Z6A7B8C9"


def bot(**env):
    from finbot.tools import InMemoryRecords
    return Container(Settings(**env), overrides={"records": InMemoryRecords(DEMO_RECORDS)}).chatbot


def ask(b, text, role="customer", tenant="m_100"):
    return b.ask(b.new_session(tenant, ["support"], role), text)


def found(domain_name, data):
    """A tool-log entry shaped like the real tool's: the domain's policy guidance is attached."""
    d = DOMAINS[domain_name]
    if (g := d.guidance(data, "customer")) is not None:
        data = {**data, "guidance": g}
    return [{"name": d.tool, "input": {d.id_arg: data[d.id_arg]}, "result": {"status": "found", "source": d.source,
                                                                             "data": data}}]


class HiddenFields(unittest.TestCase):
    """Fields outside the allow-list are stripped before the model sees the record."""

    def test_kyc_review_reason_never_reaches_the_model(self):
        t = ask(bot(), f"Why is my verification {KYC} still not done?")
        self.assertNotIn("manual_screening_queue", str(t.tools_called))
        self.assertNotIn("manual_screening_queue", t.answer)

    def test_loan_credit_score_never_reaches_the_model(self):
        t = ask(bot(), f"Why was {LOAN} declined?")
        self.assertNotIn("612", str(t.tools_called))
        self.assertNotIn("612", t.answer)
        self.assertIn("DTI_HIGH", t.answer)        # the recorded reasons ARE given


class StatusAndCauseChecks(unittest.TestCase):
    def test_wrong_current_status_is_blocked(self):
        log = found("kyc", {"case_id": KYC, "status": "in_review", "level": "enhanced"})
        reasons = domain_check(f"Good news: your case {KYC} is verified.", log)
        self.assertIn("status_mismatch:verification case:record=in_review,claimed=verified", reasons)

    def test_past_tense_about_an_event_is_not_a_status_claim(self):
        log = found("issuing", {"card_id": "card_AA11BB22", "status": "active", "form": "virtual", "last4": "1111",
                                "last_authorization": {"result": "declined", "reason_key": "CARD_FROZEN"}})
        self.assertEqual([x for x in domain_check("The card was frozen when the purchase was tried; it is "
                                                  "active now.", log) if "status_mismatch" in x], [])

    def test_wrong_cause_is_blocked_and_right_cause_passes(self):
        log = found("transfers", {"transfer_id": XFER, "status": "returned", "rail": "ach", "direction": "outgoing",
                                  "amount": "640.00", "currency": "USD", "return_code": "R02"})
        self.assertTrue(any(r.startswith("reason_mismatch:claimed=insufficient_funds")
                            for r in domain_check("It came back because of insufficient funds.", log)))
        self.assertEqual([r for r in domain_check("It was returned because the receiving account is closed.", log)
                          if r.startswith("reason_mismatch")], [])

    def test_unseen_reason_key_is_blocked(self):
        log = found("accounts", {"account_id": ACCT, "status": "active", "type": "checking", "currency": "USD",
                                 "balance": "10.00", "available_balance": "10.00"})
        self.assertIn("ungrounded_code:DOC_EXPIRED",
                      domain_check("This usually happens after a DOC_EXPIRED rejection.", log))

    def test_guessing_a_review_reason_is_blocked(self):
        log = found("kyc", {"case_id": KYC, "status": "in_review", "level": "standard"})
        self.assertTrue(any("reason_mismatch" in r for r in
                            domain_check("Your case is in review, most likely a watchlist screening hit.", log)))


class WriteRequestsBecomeTickets(unittest.TestCase):
    def test_cancel_transfer_is_a_ticket(self):
        t = ask(bot(), f"please cancel {XFER}")
        self.assertIn("ticket", t.answer.lower())
        self.assertTrue(any(r.startswith("ticket:") for r in t.reasons))

    def test_unblock_card_and_extend_loan_are_tickets(self):
        for text in ("please unblock card_AB12CD34", f"can you extend the due date on {LOAN}?"):
            with self.subTest(text=text):
                from finbot.actions import detect
                req = detect(text)
                self.assertIsNotNone(req)

    def test_ticket_lookup_uses_the_domain_tool(self):
        t = ask(bot(), f"please cancel {XFER}")
        self.assertEqual(t.tools_called[0]["name"], "get_transfer")


class Isolation(unittest.TestCase):
    def test_cross_tenant_record_does_not_exist(self):
        t = ask(bot(), f"What is the balance of {ACCT}?", tenant="m_200")
        self.assertIn("could not find", t.answer.lower())
        self.assertNotIn("2450.00", t.answer)

    def test_provenance_blocks_ids_the_user_never_typed(self):
        b = bot()
        cs = b.new_session("m_100", ["support"], "customer")
        handler = b.toolbox.handler(cs.session)
        res = b._provenance(cs, "get_account", {"account_id": ACCT})
        self.assertEqual(res["status"], "refused")
        del handler


class RecordBuiltAnswers(unittest.TestCase):
    def test_summary_from_validated_fields_only(self):
        log = found("transfers", {"transfer_id": XFER, "status": "returned", "rail": "ach", "direction": "outgoing",
                                  "amount": "640.00", "currency": "USD", "return_code": "R02",
                                  "to_account_last4": "7781"})
        out = record_answer(log, XFER)
        self.assertIn("640.00 USD", out)
        self.assertIn("R02", out)

    def test_summary_refuses_malformed_fields(self):
        log = found("transfers", {"transfer_id": XFER, "status": "returned", "rail": "ach<script>",
                                  "direction": "outgoing", "amount": "640.00", "currency": "USD",
                                  "return_code": "R02"})
        self.assertIsNone(record_answer(log, XFER))

    def test_rtp_completed_is_irrevocable_guidance(self):
        d = DOMAINS["transfers"]
        g = d.guidance({"transfer_id": "xfer_Q1", "status": "completed", "rail": "rtp"}, "customer")
        self.assertEqual(g["reason_key"], "SETTLED_IRREVOCABLE")
        self.assertFalse(g["retryable"])


class DomainConfiguration(unittest.TestCase):
    def test_payments_can_be_switched_off(self):
        b = Container(Settings(domains="accounts,transfers"),
                      overrides={"records": __import__("finbot.tools", fromlist=["InMemoryRecords"])
                                 .InMemoryRecords(DEMO_RECORDS)}).chatbot
        names = {s["name"] for s in b.toolbox.schemas()}
        self.assertEqual(names, {"get_account", "get_transfer", "search_knowledge_base"})
        t = ask(b, f"What is the balance of {ACCT}?")
        self.assertIn("2450.00", t.answer)

    def test_unknown_domain_is_an_error(self):
        from finbot.domains import enabled
        with self.assertRaises(ValueError):
            enabled("accounts,petcare")

    def test_every_prefix_resolves(self):
        for d in DOMAINS.values():
            self.assertIs(by_id(f"{d.prefix}_ABCDEF12"), d)



class MoreDomains(unittest.TestCase):
    """Disputes, mortgages, BNPL, insurance claims, subscriptions: the wrong answer each one exists to stop."""

    def test_dispute_deadline_comes_from_the_record(self):
        t = ask(bot(), "When do I have to answer dsp_D1E2F3G4?")
        self.assertIsNone(t.blocked_by, t.reasons)
        self.assertIn("2026-11-24", t.answer)
        log = found("disputes", {"dispute_id": "dsp_D1E2F3G4", "status": "needs_response", "currency": "USD",
                                 "reason_code": "PRODUCT_NOT_RECEIVED", "amount": "89.00", "respond_by": "2026-11-24"})
        from finbot.guardrails import evidence_check
        self.assertIn("ungrounded_date:2026-11-30",
                      evidence_check("You have until 2026-11-30 to respond.", log, "dsp_D1E2F3G4").reasons)

    def test_undecided_dispute_is_not_won(self):
        log = found("disputes", {"dispute_id": "dsp_D1E2F3G4", "status": "under_review", "currency": "USD",
                                 "reason_code": "DUPLICATE_CHARGE", "amount": "89.00"})
        self.assertTrue(any(r.startswith("status_mismatch") for r in
                            domain_check("Good news, the dispute was won.", log)))

    def test_mortgage_never_discusses_foreclosure(self):
        log = found("mortgages", {"mortgage_id": "mtg_H5J6K7L8", "status": "delinquent", "product": "fixed_rate"})
        self.assertTrue(any("claimed=foreclosure" in r for r in
                            domain_check("At 14 days late, foreclosure starts after 120 days.", log)))
        self.assertEqual(domain_check("The mortgage is delinquent; a person can discuss assistance options.", log), [])

    def test_bnpl_autopay_retry_date_is_stated(self):
        t = ask(bot(), "When is the next payment on bnpl_M9N8P7Q6?")
        self.assertIsNone(t.blocked_by, t.reasons)
        self.assertIn("2026-11-28", t.answer)
        self.assertIn("2026-11-18", t.answer)

    def test_event_inside_a_record_is_checked(self):
        """Found by the held-out stress test: a declined autopay restated as approved."""
        log = found("bnpl", {"plan_id": "bnpl_M9N8P7Q6", "status": "active",
                             "last_payment": {"result": "declined", "next_attempt_on": "2026-11-18"}})
        self.assertIn("event_mismatch:record=bad,claimed=good",
                      domain_check("The last automatic payment was approved.", log))
        self.assertEqual(domain_check("The last automatic payment was declined; it is tried again on 2026-11-18.",
                                      log), [])

    def test_claim_hidden_fields_and_coverage_opinions(self):
        t = ask(bot(), "Will claim clm_R5S4T3U2 be approved?")
        self.assertNotIn("pre-existing", str(t.tools_called))
        self.assertNotIn("71", str(t.tools_called))
        log = found("insurance", {"claim_id": "clm_R5S4T3U2", "status": "in_review", "policy_type": "liability",
                                  "currency": "USD", "claimed_amount": "100.00"})
        self.assertTrue(any("coverage_opinion" in r for r in domain_check("Don't worry, you're covered.", log)))
        # the worked example in docs/diagrams/finbot-gate.png: the draft is blocked, the repair passes
        self.assertEqual(domain_check("Good news, it was approved. You're covered.", log),
                         ["status_mismatch:claim:record=in_review,claimed=approved",
                          "reason_mismatch:claimed=coverage_opinion"])
        self.assertEqual(domain_check("Claim clm_R5S4T3U2 (liability) for 100.00 USD is in review; no decision has "
                                      "been made yet. No action is needed now. The decision can't be predicted in "
                                      "chat.", log), [])

    def test_paid_claim_was_approved(self):
        log = found("insurance", {"claim_id": "clm_R5S4T3U2", "status": "paid", "policy_type": "liability"})
        self.assertEqual(domain_check("It was approved and then paid.", log), [])

    def test_scheduled_cancellation_is_not_cancelled(self):
        log = found("subscriptions", {"subscription_id": "sub_V1W2X3Y4", "status": "active", "plan": "pro",
                                      "cancel_at_period_end": True})
        self.assertTrue(any(r.startswith("status_mismatch") for r in
                            domain_check("Your subscription was cancelled.", log)))
        t = ask(bot(), "Is sub_V1W2X3Y4 cancelled?")
        self.assertIsNone(t.blocked_by, t.reasons)
        self.assertIn("2026-11-30", t.answer)

    def test_change_requests_on_new_domains_are_tickets(self):
        for text in ("please pause sub_V1W2X3Y4", "can you appeal clm_R5S4T3U2?", "please accept dsp_D1E2F3G4"):
            with self.subTest(text=text):
                t = ask(bot(), text)
                self.assertIn("ticket", t.answer.lower())


if __name__ == "__main__":
    unittest.main()
