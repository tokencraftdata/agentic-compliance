"""Ideas from research, each with a test:

  argument provenance       CaMeL (Debenedetti et al. 2025), design patterns (Beurer-Kellner et al. 2025)
  multi-turn escalation     Crescendo (Russinovich et al. 2025), MHJ (Li et al. 2024)
  code-built recap          LLMs Get Lost in Multi-Turn Conversation (Laban et al. 2025)
  pass^k consistency        tau-bench (Yao et al. 2024)
"""
import json
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.container import Container  # noqa: E402
from finbot.guardrails import credential_request_check  # noqa: E402
from finbot.models import ModelOrchestrator, ModelTarget  # noqa: E402
from finbot.orchestrator import recap  # noqa: E402
from finbot.redteam import run_case  # noqa: E402
from tests.test_layers import settings  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DECLINED, OTHER_SAME_TENANT = "txn_A1B2C3D4", "txn_E5F6G7H8"


def cases(name):
    with open(os.path.join(ROOT, "data", name)) as f:
        return [json.loads(line) for line in f if line.strip()]


class FetchesThenAnswers:
    """Asks for one transaction, then answers."""

    def __init__(self, txn_id):
        self.txn_id, self.calls = txn_id, 0

    def create(self, system, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="get_transaction",
                                                          input={"txn_id": self.txn_id})])
        return NS(stop_reason="end_turn", content=[NS(type="text", text="Here is what I found.")])


def bot(model):
    c = Container(settings(), overrides={"engine": ModelOrchestrator([ModelTarget("m", model, attempts=1)],
                                                                     sleep=lambda s: None)})
    c.chatbot.sleep = c.toolbox.sleep = lambda s: None
    return c.chatbot


class Provenance(unittest.TestCase):
    def test_id_the_user_never_typed_is_refused_even_in_the_same_tenant(self):
        b = bot(FetchesThenAnswers(OTHER_SAME_TENANT))
        t = b.ask(b.new_session("m_100", ["support"]), f"why did {DECLINED} fail?")
        (call,) = t.tools_called
        self.assertEqual((call["input"]["txn_id"], call["result"]["status"]), (OTHER_SAME_TENANT, "refused"))

    def test_id_the_user_typed_earlier_is_allowed(self):
        b = bot(FetchesThenAnswers(DECLINED))
        cs = b.new_session("m_100", ["support"])
        b.ask(cs, f"why did {DECLINED} fail?")
        b.llm = ModelOrchestrator([ModelTarget("m", FetchesThenAnswers(DECLINED), attempts=1)], sleep=lambda s: None)
        t = b.ask(cs, "can I retry it?")              # no id in this message: it came from the user before
        self.assertEqual(t.tools_called[0]["result"]["status"], "found")

    def test_pivot_attacks(self):
        pivots = [c for c in cases("redteam.jsonl") if c["attack"] == "pivot"]
        self.assertEqual(len(pivots), 2)
        for c in pivots:
            with self.subTest(case=c["id"]):
                self.assertTrue(run_case(c, "defended")["ok"])
                self.assertTrue(run_case(c, "no_screening")["ok"])   # screening misses it; provenance holds
                self.assertTrue(run_case(c, "undefended")["success"])  # the harness really follows pivots


class MultiTurn(unittest.TestCase):
    def test_escalating_attacks_are_stopped_by_the_harmful_turn(self):
        for c in cases("redteam_multiturn.jsonl"):
            with self.subTest(case=c["id"]):
                r = run_case(c, "defended")
                self.assertTrue(r["ok"])
                if not c["benign"] and r["first_block_turn"] is not None:
                    self.assertLessEqual(r["first_block_turn"], r["harmful_turn"])

    def test_negation_in_an_earlier_sentence_does_not_excuse_a_credential_request(self):
        self.assertEqual(credential_request_check("I won't guess. Please send your full card number."),
                         ["credential_request"])
        self.assertEqual(credential_request_check("Please never share your PIN. Send your card number."),
                         ["credential_request"])
        self.assertEqual(credential_request_check("We never ask you to send your card number."), [])


class Recap(unittest.TestCase):
    def test_recap_is_what_the_user_typed_newest_kept_and_short(self):
        history = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "model prose"},
                   {"role": "user", "content": "<verifier>repair</verifier>"},
                   {"role": "user", "content": "x" * 500}, {"role": "user", "content": [{"type": "tool_result"}]}]
        out = recap(history, ["older"], keep=3)
        self.assertEqual(out[:2], ["older", "first"])
        self.assertEqual(len(out[2]), 200)
        self.assertNotIn("model prose", out)

    def test_rollover_carries_the_recap_into_the_new_thread(self):
        c = Container(settings(max_turns_per_thread=1))
        b = c.chatbot
        cs = b.new_session("m_100", ["support"])
        b.ask(cs, f"why did {DECLINED} fail?")
        b.ask(cs, "how do refunds work?")                  # rolls over
        self.assertIn(f"why did {DECLINED} fail?", cs.memory.recap)
        self.assertIn("earlier_requests_in_this_conversation", cs.memory.render())


class Consistency(unittest.TestCase):
    def test_pass_k_on_the_dev_set(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import run_consistency
        self.assertEqual(run_consistency.stable_seed("a", 1), run_consistency.stable_seed("a", 1))
        r = run_consistency.run(os.path.join(ROOT, "data"), k=3)
        self.assertEqual((r["pass_k"], r["inconsistent"]), (1.0, []))


if __name__ == "__main__":
    unittest.main()
