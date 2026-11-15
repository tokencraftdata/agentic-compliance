"""Processor orchestration: adapters, routing rules, cascade policy, engine, simulator,
the bot's routing tools and the routing evidence checks."""
import json
import os
import random
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.guardrails import evidence_check  # noqa: E402
from finbot.processors import (  # noqa: E402
    Attempt,
    CascadePolicy,
    Incident,
    Outcome,
    Payment,
    PaymentOrchestrator,
    ProcessorDirectory,
    ProcessorUnavailable,
    Router,
)
from finbot.processors.adapters import ADAPTERS  # noqa: E402
from finbot.processors.codes import CANONICAL, UNMAPPED  # noqa: E402
from finbot.processors.explain import health_view, routing_view  # noqa: E402
from finbot.processors.simulator import SCENARIOS, build_orchestrator, run_scenario  # noqa: E402
from finbot.tools import InMemoryLogs, InMemoryTxns, Session, ToolBox  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
PAY = Payment("txn_TEST000001", "10.00", "USD", "visa", "US")
DAY = "2026-11-20T10:00:00Z"


def attempt(code, n=1, processor="northstar"):
    from finbot.processors.codes import outcome_of
    return Attempt(n, processor, code, code, outcome_of(code), 100)


class Adapters(unittest.TestCase):
    def test_every_canonical_code_round_trips_through_every_dialect(self):
        for name in ADAPTERS.names():
            adapter = ADAPTERS.create(name)
            for code in CANONICAL:
                self.assertEqual(adapter.decode(adapter.encode(code)).code, code, f"{name} {code}")

    def test_dialects_differ_but_mean_the_same(self):
        raws = {"northstar": {"resp": "51"}, "meridian": {"result": "Refused", "refusalReason": "Not enough balance"},
                "keystone": {"http": 402, "error": {"decline_code": "insufficient_funds"}}}
        for name, raw in raws.items():
            n = ADAPTERS.create(name).decode(raw)
            self.assertEqual((n.code, n.outcome), ("51", Outcome.SOFT_DECLINE), name)

    def test_unknown_responses_are_unmapped_never_guessed(self):
        for name, raw in (("northstar", {"resp": "Q7"}), ("meridian", {"result": "Refused", "refusalReason": "?"}),
                          ("keystone", {"http": 402, "error": {"decline_code": "new_reason"}})):
            n = ADAPTERS.create(name).decode(raw)
            self.assertEqual((n.code, n.outcome), (UNMAPPED, Outcome.UNMAPPED), name)


class Routing(unittest.TestCase):
    def setUp(self):
        self.router = Router(ProcessorDirectory.default())

    def test_capability_then_local_then_cost(self):
        plan = self.router.plan(Payment("t", "1.00", "CAD", "amex", "CA"), lambda n: True)
        self.assertEqual(plan.candidates, ("meridian",))            # only meridian takes CAD and amex
        self.assertIn("keystone: no CAD", plan.steps[0].detail)
        plan = self.router.plan(PAY, lambda n: True)
        self.assertEqual(plan.candidates[0], "northstar")          # local in US and cheapest
        plan = self.router.plan(Payment("t", "1.00", "GBP", "visa", "GB"), lambda n: True)
        self.assertEqual(plan.candidates, ("meridian", "keystone"))  # local acquiring beats cost

    def test_health_rule_skips_open_circuits_and_says_so(self):
        plan = self.router.plan(PAY, lambda n: n != "northstar")
        self.assertNotIn("northstar", plan.candidates)
        self.assertIn("northstar skipped: circuit open", [s.detail for s in plan.steps])

    def test_steps_only_recorded_when_a_rule_changes_something(self):
        plan = self.router.plan(PAY, lambda n: True)                 # every processor takes USD visa
        self.assertEqual([s.rule for s in plan.steps], ["local"])    # only local acquiring reorders
        self.assertEqual(plan.steps[0].detail, "local acquiring in US first: northstar")


class Cascade(unittest.TestCase):
    def setUp(self):
        self.policy = CascadePolicy(max_attempts=3)

    def test_issuer_declines_and_timeouts_never_cascade(self):
        for code in ("05", "51", "59", "PROCESSOR_TIMEOUT", "00"):
            self.assertFalse(self.policy.decide(attempt(code), [attempt(code)], ["meridian"]).allowed, code)
        self.assertIn("double charge", self.policy.decide(attempt("PROCESSOR_TIMEOUT"), [], ["x"]).reason)

    def test_processor_errors_cascade_until_the_limit(self):
        self.assertTrue(self.policy.decide(attempt("PROCESSOR_ERROR"), [attempt("PROCESSOR_ERROR")], ["m"]).allowed)
        three = [attempt("91", n) for n in (1, 2, 3)]
        self.assertFalse(self.policy.decide(three[-1], three, ["m"]).allowed)
        self.assertFalse(self.policy.decide(attempt("PROCESSOR_ERROR"), [attempt("PROCESSOR_ERROR")], []).allowed)

    def test_invalid_limit_rejected(self):
        with self.assertRaises(ValueError):
            CascadePolicy(0)


class FakeClient:
    def __init__(self, adapter, code="00", down=False):
        self.adapter, self.code, self.down, self.keys = adapter, code, down, []

    def authorize(self, payment, key, at):
        self.keys.append(key)
        if self.down:
            raise ProcessorUnavailable("refused")
        return self.adapter.encode(self.code), 50


class Engine(unittest.TestCase):
    def build(self, **codes):
        d = ProcessorDirectory.default()
        adapters = {n: ADAPTERS.create(n) for n in d.names()}
        clients = {n: FakeClient(adapters[n], **codes.get(n, {})) for n in d.names()}
        return PaymentOrchestrator(clients, adapters, Router(d), CascadePolicy()), clients

    def test_processor_down_cascades_to_next_with_its_own_idempotency_key(self):
        orch, clients = self.build(northstar={"down": True})
        trace = orch.authorize(PAY, DAY)
        self.assertEqual([(a.processor, a.code, a.cascaded) for a in trace.attempts],
                         [("northstar", "PROCESSOR_ERROR", True), ("keystone", "00", False)])
        self.assertEqual(clients["keystone"].keys, [f"{PAY.txn_id}:keystone"])

    def test_issuer_decline_and_timeout_stop_at_the_first_processor(self):
        for code in ("05", "PROCESSOR_TIMEOUT"):
            orch, clients = self.build(northstar={"code": code})
            trace = orch.authorize(PAY, DAY)
            self.assertEqual(len(trace.attempts), 1, code)
            self.assertEqual(clients["keystone"].keys, [], code)

    def test_breaker_opens_during_an_outage_and_routing_skips_the_processor(self):
        orch, clients = self.build(northstar={"down": True})
        for i in range(5):
            orch.authorize(Payment(f"txn_T{i:09d}", "1.00", "USD", "visa", "US"), DAY)
        self.assertEqual(len(clients["northstar"].keys), 3)          # threshold 3, then skipped
        trace = orch.authorize(Payment("txn_T999999999", "1.00", "USD", "visa", "US"), DAY)
        self.assertEqual(trace.attempts[0].processor, "keystone")
        self.assertIn("northstar skipped: circuit open", [s.detail for s in trace.plan.steps])

    def test_issuer_declines_do_not_trip_the_breaker(self):
        orch, _ = self.build(northstar={"code": "05"})
        for i in range(10):
            orch.authorize(Payment(f"txn_D{i:09d}", "1.00", "USD", "visa", "US"), DAY)
        self.assertEqual(orch.breakers["northstar"].state, "closed")

    def test_no_eligible_processor_is_not_routed(self):
        orch, _ = self.build()
        trace = orch.authorize(Payment("t", "1.00", "JPY", "visa", "US"), DAY)
        self.assertFalse(trace.routed)
        self.assertEqual(trace.to_dict()["routed"], False)

    def test_open_breaker_is_not_called_even_if_planned(self):
        orch, clients = self.build()
        for _ in range(3):
            orch.breakers["northstar"].failure()          # opens after planning would have seen it closed
        a = orch._attempt("northstar", PAY, DAY, 1)
        self.assertEqual((a.code, a.raw_code, clients["northstar"].keys), ("PROCESSOR_ERROR", "circuit open", []))

    def test_missing_adapter_is_a_wiring_error(self):
        d = ProcessorDirectory.default()
        with self.assertRaises(ValueError):
            PaymentOrchestrator({}, {}, Router(d), CascadePolicy())


class Simulator(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = run_scenario(SCENARIOS["steady"], 300, seed=3).as_dict()
        self.assertEqual(a, run_scenario(SCENARIOS["steady"], 300, seed=3).as_dict())

    def test_issuer_answers_the_same_on_every_processor(self):
        d = ProcessorDirectory(ProcessorDirectory.default().profiles,
                               [Incident("northstar", "2026-11-20T00:00:00Z", "2026-11-21T00:00:00Z", "outage", "x")])
        orch = build_orchestrator(d, lambda p: "51", random.Random(1))
        trace = orch.authorize(PAY, DAY)
        self.assertEqual([a.code for a in trace.attempts], ["PROCESSOR_ERROR", "51"])

    def test_outage_is_absorbed_but_an_issuer_outage_is_not(self):
        steady = run_scenario(SCENARIOS["steady"], 2000)
        outage = run_scenario(SCENARIOS["northstar_outage"], 2000)
        issuer = run_scenario(SCENARIOS["issuer_outage"], 2000)
        self.assertLess(abs(steady.approval_rate - outage.approval_rate), 0.01)   # failover works
        self.assertLess(outage.by_processor["northstar"]["processor_errors"], 25)  # breaker limits the damage
        self.assertLess(issuer.approval_rate, steady.approval_rate - 0.1)          # cascading can't fix issuers
        self.assertGreater(issuer.cascaded, steady.cascaded * 5)

    def test_cli(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "simulate_routing.py"), "-n", "200",
                            "--json"], capture_output=True, text=True, check=True)
        self.assertEqual({x["scenario"] for x in json.loads(r.stdout)}, set(SCENARIOS))


class ReadSide(unittest.TestCase):
    REC = {"txn_id": "txn_A", "tenant_id": "m_1", "created_at": DAY, "routing": {"routed": True, "attempts": [
        {"n": 1, "processor": "northstar", "code": "PROCESSOR_ERROR", "cascaded": True},
        {"n": 2, "processor": "keystone", "code": "05", "cascaded": False}]}}

    def test_routing_view_adds_code_computed_advice(self):
        v = routing_view(self.REC)
        self.assertFalse(v["cascade_advice"]["another_processor_would_help"])
        self.assertEqual(v["cascade_advice"]["final_processor"], "keystone")
        self.assertIsNone(routing_view({"txn_id": "x"}))

    def test_health_view_counts_only_the_records_it_is_given(self):
        v = health_view("northstar", ProcessorDirectory.default(), [self.REC])
        self.assertEqual((v["your_attempts"], v["your_processor_errors"]), (1, 1))
        self.assertIsNone(health_view("nope", ProcessorDirectory.default(), []))

    def test_tools_are_tenant_scoped(self):
        other = {**self.REC, "txn_id": "txn_OTHER1", "tenant_id": "m_2"}
        box = ToolBox(InMemoryTxns([self.REC, other]), InMemoryLogs([]), None, sleep=lambda s: None,
                      processors=ProcessorDirectory.default())
        run = box.handler(Session("s", "m_1", ["support"]))
        self.assertEqual(run("get_routing_trace", {"txn_id": "txn_OTHER1"})["status"], "not_found")
        self.assertEqual(run("get_routing_trace", {"txn_id": "txn_A"})["status"], "found")
        health = run("get_processor_health", {"processor": "northstar"})["data"]
        self.assertEqual(health["your_attempts"], 1)                  # m_2's attempt is not counted
        self.assertEqual([r["txn_id"] for r in box.txns.recent("m_1", 1)], ["txn_A"])
        names = {s["name"] for s in box.schemas()}
        self.assertTrue({"get_routing_trace", "get_processor_health"} <= names)
        self.assertNotIn("get_routing_trace", {s["name"] for s in ToolBox(InMemoryTxns([]), InMemoryLogs([]),
                                                                         None).schemas()})


def trace_log(attempts, advice_ok=False):
    return [{"name": "get_routing_trace", "input": {"txn_id": "txn_A"},
             "result": {"status": "found", "source": "routing",
                        "data": {"txn_id": "txn_A", "routed": True, "attempts": attempts,
                                 "cascade_advice": {"another_processor_would_help": advice_ok}}}}]


class RoutingEvidence(unittest.TestCase):
    ONE = [{"n": 1, "processor": "northstar", "code": "05", "cascaded": False}]
    TWO = [{"n": 1, "processor": "northstar", "code": "PROCESSOR_ERROR", "cascaded": True},
           {"n": 2, "processor": "keystone", "code": "00", "cascaded": False}]

    def reasons(self, answer, log):
        return evidence_check(answer, log, "txn_A").reasons

    def test_processor_must_appear_in_evidence(self):
        self.assertIn("ungrounded_processor:meridian", self.reasons("It went through meridian.", trace_log(self.ONE)))
        self.assertEqual(self.reasons("It was routed through northstar.", trace_log(self.ONE)), [])

    def test_cascade_claims_need_a_cascaded_attempt(self):
        self.assertIn("cascade_claim_mismatch:record=no_cascade",
                      self.reasons("It cascaded to another processor.", trace_log(self.ONE)))
        self.assertIn("ungrounded_cascade_claim", self.reasons("It failed over to a backup processor.", []))
        self.assertEqual(self.reasons("On northstar it cascaded to keystone.", trace_log(self.TWO)), [])

    def test_cascade_advice_must_match_policy(self):
        bad = "You should send it to another processor."
        self.assertIn("cascade_advice_contradicts_policy", self.reasons(bad, trace_log(self.ONE)))
        self.assertEqual(self.reasons(bad, trace_log(self.ONE, advice_ok=True)), [])
        self.assertEqual(self.reasons("Sending it to another processor would not help.", trace_log(self.ONE)), [])

    def test_outage_claims_need_an_incident_or_a_processor_error(self):
        self.assertIn("ungrounded_outage_claim:northstar",
                      self.reasons("northstar was down at the time.", trace_log(self.ONE)))
        self.assertEqual(self.reasons("northstar was down, so it cascaded to keystone.", trace_log(self.TWO)), [])
        self.assertIn("ungrounded_outage_claim:keystone",
                      self.reasons("It cascaded from northstar because keystone was down.", trace_log(self.TWO)))

    def test_gateway_logs_count_as_routing_evidence(self):
        logs = [{"name": "search_transaction_logs", "input": {"txn_id": "txn_A", "limit": 10},
                 "result": {"status": "found", "source": "log_search", "data": [
                     {"txn_id": "txn_A", "event": "processor_attempt", "processor": "keystone",
                      "code": "PROCESSOR_ERROR"},
                     {"txn_id": "txn_A", "event": "cascade", "from": "keystone", "reason": "PROCESSOR_ERROR"}]}}]
        self.assertEqual(self.reasons("It cascaded after keystone was down.", logs), [])

    def test_cascade_claim_is_checked_against_the_named_transaction(self):
        a, b = trace_log(self.ONE), trace_log(self.TWO)
        a[0]["input"]["txn_id"] = a[0]["result"]["data"]["txn_id"] = "txn_AAAAAA"
        b[0]["input"]["txn_id"] = b[0]["result"]["data"]["txn_id"] = "txn_BBBBBB"
        log = a + b
        self.assertIn("cascade_claim_mismatch:record=no_cascade",
                      self.reasons("txn_AAAAAA cascaded to a backup processor.", log))
        self.assertNotIn("cascade_claim_mismatch:record=no_cascade",
                         self.reasons("txn_BBBBBB cascaded to a backup processor.", log))

    def test_negation_in_the_previous_sentence_does_not_cancel_a_claim(self):
        answer = "I won't guess. It failed over to a backup processor first."
        self.assertIn("ungrounded_cascade_claim", evidence_check(answer, [], None).reasons)


class IngestionSpecs(unittest.TestCase):
    def test_spec_parsing_errors_are_clear_and_trailing_slash_is_an_empty_prefix(self):
        from finbot.ingestion import _pair
        self.assertEqual(_pair("bucket", "/", "bucket[/prefix]", right_optional=True), ("bucket", ""))
        self.assertEqual(_pair("bucket/", "/", "bucket[/prefix]", right_optional=True), ("bucket", ""))
        self.assertEqual(_pair("https://h.example/idx", "/", "endpoint/index", last=True),
                         ("https://h.example", "idx"))
        with self.assertRaisesRegex(ValueError, "site_id/drive_id"):
            _pair("only-site", "/", "site_id/drive_id")


class GeneratedData(unittest.TestCase):
    def test_every_trace_ends_where_the_record_says(self):
        for d in ("data", "data/test"):
            with open(os.path.join(ROOT, d, "transactions.jsonl")) as f:
                for t in map(json.loads, f):
                    attempts = t["routing"].get("attempts") or []
                    if not attempts:
                        self.assertNotIn("processor", t)
                        continue
                    want = t.get("decline_code") or ("00" if t["status"] == "approved" else t.get("error"))
                    self.assertEqual(attempts[-1]["code"], want, t["txn_id"])
                    self.assertEqual(attempts[-1]["processor"], t["processor"])
                    if t.get("error") == "PROCESSOR_ERROR":
                        self.assertFalse({"decline_code", "auth_code", "settlement"} & set(t), t["txn_id"])

    def test_catalog_written_with_the_data_loads(self):
        for d in ("data", "data/test"):
            directory = ProcessorDirectory.load(os.path.join(ROOT, d))
            self.assertEqual(set(directory.names()), {"northstar", "meridian", "keystone"})
            self.assertEqual(len(directory.incidents), 1)


if __name__ == "__main__":
    unittest.main()
