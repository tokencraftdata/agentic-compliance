"""Release process: version consistency, the release gate's parsers, canary decisions and the
rollout driver (scripts/release_check.py, finbot/rollout.py, scripts/rollout.py)."""
import importlib.util
import json
import os
import sys
import tomllib
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

import finbot  # noqa: E402
from finbot.rollout import Metrics, decide, metrics_from_audit  # noqa: E402


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Version(unittest.TestCase):
    def test_package_pyproject_and_changelog_agree(self):
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            self.assertEqual(tomllib.load(f)["project"]["version"], finbot.__version__)
        with open(os.path.join(ROOT, "CHANGELOG.md")) as f:
            top = next(ln for ln in f if ln.startswith("## ["))
        self.assertIn(f"[{finbot.__version__}]", top)


class GateParsers(unittest.TestCase):
    def setUp(self):
        self.g = load("release_check")

    def test_parsers(self):
        self.assertTrue(self.g._all_passed(0, "185/185 passed")[0])
        self.assertFalse(self.g._all_passed(0, "184/185 passed")[0])
        table = "mode injected repaired fallback LEAKED\nstatus_flip 41 41 0 0\nfake_ticket 41 40 0 1\n"
        self.assertEqual(self.g._leaked_zero(0, table), (False, "1 of 82 leaked"))
        self.assertTrue(self.g._blocked_zero(0, "TOTAL 2876 asked 0 blocked")[0])
        self.assertFalse(self.g._defended_zero(0, "defended          1/34")[0])
        self.assertEqual(self.g._pass_k(0, "pass^1 = 1.000   pass^16 = 0.938"), (False, "pass^16 = 0.938"))
        self.assertTrue(self.g._scenarios(0, "8/8 scenarios passed")[0])

    def test_version_gate(self):
        self.assertTrue(self.g._version()[0])


class Canary(unittest.TestCase):
    base = Metrics(turns=5000, refusal_rate=0.08, unavailable_rate=0.01, unsafe_output_rate=0.001, p95_ms=4000)

    def test_healthy_canary_is_promoted(self):
        self.assertEqual(decide(Metrics(turns=600, refusal_rate=0.09, unavailable_rate=0.012,
                                        unsafe_output_rate=0.001, p95_ms=4200), self.base).action, "promote")

    def test_any_card_leak_rolls_back_at_once(self):
        d = decide(Metrics(turns=3, egress_leaks=1), self.base)
        self.assertEqual((d.action, d.reasons), ("rollback", ["egress_leak:1"]))

    def test_too_little_traffic_waits(self):
        self.assertEqual(decide(Metrics(turns=50), self.base).action, "wait")

    def test_each_regression_rolls_back(self):
        for field, value in [("refusal_rate", 0.2), ("unavailable_rate", 0.05), ("unsafe_output_rate", 0.02),
                             ("p95_ms", 9000)]:
            m = Metrics(turns=600, refusal_rate=0.08, unavailable_rate=0.01, unsafe_output_rate=0.001, p95_ms=4000)
            setattr(m, field, value)
            d = decide(m, self.base)
            self.assertEqual(d.action, "rollback", field)
            self.assertTrue(d.reasons[0].startswith(field))

    def test_metrics_from_audit_lines(self):
        lines = [json.dumps({"stream": "turn", "version": "0.4.0", "blocked_by": b, "latency_ms": ms})
                 for b, ms in [(None, 100), ("evidence_guard", 200), ("unavailable", 300), (None, 400)]]
        lines.append(json.dumps({"stream": "security", "class": "unsafe_output", "version": "0.4.0"}))
        m = metrics_from_audit(lines)["0.4.0"]
        self.assertEqual((m.turns, m.refusal_rate, m.unavailable_rate, m.unsafe_output_rate), (4, 0.25, 0.25, 0.25))


class FakeKube:
    def __init__(self, stable=10, canary=0):
        self.rep = {"finbot-worker": stable, "finbot-worker-canary": canary}
        self.images, self.calls = {}, []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("get", "deployment"):
            return str(self.rep[args[2]])
        if args[0] == "scale":
            self.rep[args[2]] = int(args[3].split("=")[1])
        if args[:2] == ("set", "image"):
            self.images[args[2].split("/")[1]] = args[3].split("=")[1]
        return ""


class RolloutDriver(unittest.TestCase):
    def setUp(self):
        self.r = load("rollout")
        self.healthy = {"turns": 600, "refusal_rate": 0.08, "unavailable_rate": 0.01, "p95_ms": 4000}

    def run_with(self, canary_metrics):
        kube, log = FakeKube(), []
        seq = iter(canary_metrics)
        result = self.r.rollout("img@sha256:new", [10, 50, 100], 0,
                                lambda: {"canary": next(seq), "baseline": self.healthy},
                                run=kube, sleep=lambda s: None, log=log.append)
        return result, kube

    def test_promotes_through_every_step(self):
        result, kube = self.run_with([self.healthy] * 3)
        self.assertEqual(result, "promoted")
        self.assertEqual(kube.images["finbot-worker"], "img@sha256:new")
        self.assertEqual(kube.rep, {"finbot-worker": 10, "finbot-worker-canary": 0})

    def test_rolls_back_and_leaves_stable_untouched(self):
        result, kube = self.run_with([self.healthy, {**self.healthy, "egress_leaks": 1}])
        self.assertEqual(result, "rolled_back")
        self.assertNotIn("finbot-worker", kube.images)                 # stable kept the old image
        self.assertEqual(kube.rep, {"finbot-worker": 10, "finbot-worker-canary": 0})

    def test_ten_percent_of_ten_pods_is_one_canary(self):
        kube = FakeKube()
        seq = iter([{**self.healthy, "egress_leaks": 1}])
        self.r.rollout("img", [10], 0, lambda: {"canary": next(seq), "baseline": self.healthy},
                       run=kube, sleep=lambda s: None, log=lambda m: None)
        scales = [c for c in kube.calls if c[0] == "scale"]
        self.assertEqual(scales[0], ("scale", "deployment", "finbot-worker-canary", "--replicas=1"))


if __name__ == "__main__":
    unittest.main()
