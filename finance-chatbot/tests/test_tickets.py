"""Write requests become tickets for people; the bot never executes, and never invents tickets."""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.actions import detect  # noqa: E402
from finbot.container import Container  # noqa: E402
from finbot.faults import HallucinatingLLM  # noqa: E402
from finbot.llm import FakeLLM  # noqa: E402
from finbot.models import ModelOrchestrator, ModelTarget  # noqa: E402
from finbot.replies import CARD_NOTICE  # noqa: E402
from finbot.security import Forbidden, Principal  # noqa: E402
from finbot.tickets import DONE, JsonFileTickets, TicketDesk  # noqa: E402
from finbot.verification import TicketVerifier  # noqa: E402
from tests.test_layers import settings  # noqa: E402

DECLINED, APPROVED, OTHER_TENANT = "txn_A1B2C3D4", "txn_E5F6G7H8", "txn_OTHER999"
SUPPORT = Principal("agent-7", "internal", "support")
MERCHANT = Principal("u1", "m_100", "merchant")


class NoModel:
    """Fails the test if a write request ever reaches the model."""

    def create(self, system, messages, tools):
        raise AssertionError("write requests must be handled in code, not by the model")


def bot(model=None):
    over = {"engine": ModelOrchestrator([ModelTarget("m", model, attempts=1)], sleep=lambda s: None)} if model else {}
    c = Container(settings(), overrides=over)
    c.chatbot.sleep = c.toolbox.sleep = lambda s: None
    return c


class Detection(unittest.TestCase):
    REQUESTS = {
        f"please refund {DECLINED}": ("refund", DECLINED),
        f"can you cancel {APPROVED}?": ("cancel", APPROVED),
        f"I want a refund for {APPROVED}": ("refund", APPROVED),
        f"Refund {DECLINED} now": ("refund", DECLINED),
        "could you retry it": ("retry", "txn_FOCUS01"),
        "please update my bank details": ("update_details", None),
        "can you raise my limit": ("limit_change", None),
        "I'd like a fee waiver": ("fee_waiver", None),
    }
    QUESTIONS = ["how do refunds work?", "can I refund a partial amount?", "refund policy?",
                 f"Why did {DECLINED} fail?", f"Should I retry {DECLINED}?", "Retry rules for 51",
                 "Will the refund go through?", f"Can you check {DECLINED}?", "what does cancel mean"]

    def test_requests(self):
        for text, (kind, txn) in self.REQUESTS.items():
            with self.subTest(text=text):
                r = detect(text, "txn_FOCUS01")
                self.assertEqual((r.kind, r.txn_id), (kind, txn))

    def test_questions_are_not_requests(self):
        for text in self.QUESTIONS:
            with self.subTest(text=text):
                self.assertIsNone(detect(text, "txn_FOCUS01"))


class TicketTurns(unittest.TestCase):
    def test_request_opens_ticket_without_calling_the_model(self):
        c = bot(NoModel())
        cs = c.chatbot.new_session("m_100", ["support"], "merchant")
        t = c.chatbot.ask(cs, f"please refund {APPROVED}")
        (ticket,) = c.tickets.all()
        self.assertTrue(t.escalated)
        self.assertIn(ticket.id, t.answer)
        self.assertIn("nothing has been changed yet", t.answer)
        self.assertEqual((ticket.kind, ticket.txn_id, ticket.tenant_id, ticket.status),
                         ("refund", APPROVED, "m_100", "open"))
        self.assertEqual(ticket.facts["status"], "approved")
        self.assertIn(ticket.id, cs.memory.render())

    def test_same_request_twice_is_one_ticket(self):
        c = bot(NoModel())
        cs = c.chatbot.new_session("m_100", ["support"], "merchant")
        a = c.chatbot.ask(cs, f"please refund {APPROVED}")
        b = c.chatbot.ask(cs, f"can you refund {APPROVED}?")
        self.assertEqual(len(c.tickets.all()), 1)
        self.assertIn("already open", b.answer)
        self.assertEqual(a.reasons[-1], b.reasons[-1])

    def test_other_tenants_transaction_gets_no_ticket(self):
        c = bot(NoModel())
        t = c.chatbot.ask(c.chatbot.new_session("m_100", ["support"], "merchant"), f"please refund {OTHER_TENANT}")
        self.assertEqual((t.blocked_by, c.tickets.all()), ("write_request", []))
        self.assertIn("couldn't find", t.answer)

    def test_retry_of_non_retryable_decline_carries_the_policy(self):
        c = bot(NoModel())
        t = c.chatbot.ask(c.chatbot.new_session("m_100", ["support"], "merchant"), f"please retry {DECLINED}")
        (ticket,) = c.tickets.all()
        self.assertIn("not retryable", ticket.policy_note)
        self.assertIn("shouldn't be retried", t.answer)

    def test_card_in_request_is_masked_and_user_is_told(self):
        c = bot(NoModel())
        t = c.chatbot.ask(c.chatbot.new_session("m_100", ["support"], "merchant"),
                          f"please refund {APPROVED} to card 4111 1111 1111 1111")
        self.assertTrue(t.answer.startswith(CARD_NOTICE))
        self.assertNotIn("4111 1111", c.tickets.all()[0].request_text)

    def test_card_notice_on_an_ordinary_question(self):
        c = bot()
        t = c.chatbot.ask(c.chatbot.new_session("m_100", ["support"]), "my card 4111 1111 1111 1111 was declined, why?")
        self.assertTrue(t.answer.startswith(CARD_NOTICE))


class InventedTickets(unittest.TestCase):
    def test_verifier(self):
        v = TicketVerifier(lambda: {"TCK-AAAA1111"})
        self.assertEqual(v.check("Your ticket TCK-AAAA1111 is open.", [], None).action, "allow")
        self.assertEqual(v.check("See TCK-BBBB2222.", [], None).reasons, ["ungrounded_ticket:TCK-BBBB2222"])
        none = TicketVerifier(set)
        self.assertEqual(none.check("I've raised a ticket for this.", [], None).reasons, ["claims_ticket_created"])
        self.assertEqual(none.check("A person can raise a ticket for you.", [], None).action, "allow")

    def test_model_inventing_a_ticket_never_reaches_the_user(self):
        c = bot(HallucinatingLLM(FakeLLM(), "fake_ticket"))
        t = c.chatbot.ask(c.chatbot.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        self.assertNotIn("TCK-9F3A21C7", t.answer)

    def test_model_may_mention_a_real_ticket(self):
        c = bot()
        cs = c.chatbot.new_session("m_100", ["support"], "merchant")
        c.chatbot.ask(cs, f"please refund {APPROVED}")
        real = c.tickets.all()[0].id

        class Mentions:
            def create(self, system, messages, tools):
                return NS(stop_reason="end_turn", content=[NS(type="text", text=f"Ticket {real} is with our team.")])
        c.chatbot.llm = ModelOrchestrator([ModelTarget("m", Mentions(), attempts=1)], sleep=lambda s: None)
        t = c.chatbot.ask(cs, "what happens next?")
        self.assertIsNone(t.blocked_by)
        self.assertIn(real, t.answer)


class Desk(unittest.TestCase):
    def setUp(self):
        self.c = bot(NoModel())
        self.c.chatbot.ask(self.c.chatbot.new_session("m_100", ["support"], "merchant"), f"please refund {APPROVED}")
        self.tid = self.c.tickets.all()[0].id
        self.desk: TicketDesk = self.c.ticket_desk

    def test_people_claim_and_resolve_with_a_note(self):
        self.assertEqual([t.id for t in self.desk.list(SUPPORT)], [self.tid])
        self.desk.claim(SUPPORT, self.tid)
        with self.assertRaises(ValueError):
            self.desk.resolve(SUPPORT, self.tid, DONE, "  ")
        t = self.desk.resolve(SUPPORT, self.tid, DONE, "refunded in the payment console, ref R-123")
        self.assertEqual((t.status, t.assignee), (DONE, "agent-7"))
        with self.assertRaises(ValueError):
            self.desk.resolve(SUPPORT, self.tid, DONE, "again")

    def test_merchants_read_only_their_own_and_cannot_resolve(self):
        self.assertEqual(self.desk.get(MERCHANT, self.tid).id, self.tid)
        with self.assertRaises(Forbidden):
            self.desk.get(Principal("u2", "m_200", "merchant"), self.tid)
        with self.assertRaises(Forbidden):
            self.desk.resolve(MERCHANT, self.tid, DONE, "done it myself")
        with self.assertRaises(Forbidden):
            self.desk.list(MERCHANT)

    def test_file_store_survives_a_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "tickets.json")
        store = JsonFileTickets(path)
        store.create_if_absent(self.c.tickets.all()[0])
        self.assertEqual(JsonFileTickets(path).get(self.tid).kind, "refund")


if __name__ == "__main__":
    unittest.main()
