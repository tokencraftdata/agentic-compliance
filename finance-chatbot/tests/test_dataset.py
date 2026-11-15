"""Synthetic datasets: dev and held-out test splits are deterministic, disjoint, and pass."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.guardrails import evidence_check  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
GEN = os.path.join(ROOT, "scripts", "generate_synthetic.py")


def jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def gen(*args, out):
    subprocess.run([sys.executable, GEN, *args, "--out", out], check=True, capture_output=True)
    with open(os.path.join(out, "MANIFEST.json")) as f:
        return json.load(f)["sha256"]


class Splits(unittest.TestCase):
    def test_committed_files_match_their_generator(self):
        for split, d in (("dev", "data"), ("test", "data/test")):
            with tempfile.TemporaryDirectory() as tmp:
                fresh = gen("--split", split, out=tmp)
            for name, digest in fresh.items():
                with open(os.path.join(ROOT, d, name), "rb") as f:
                    self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digest, f"{d}/{name} is stale")

    def test_test_split_is_disjoint_from_dev(self):
        dev = jsonl(os.path.join(ROOT, "data/transactions.jsonl"))
        test = jsonl(os.path.join(ROOT, "data/test/transactions.jsonl"))
        self.assertFalse({t["tenant_id"] for t in dev} & {t["tenant_id"] for t in test})
        self.assertFalse({t["txn_id"] for t in dev} & {t["txn_id"] for t in test})

    def test_adversarial_labels_point_at_real_records(self):
        txns = {t["txn_id"]: t for t in jsonl(os.path.join(ROOT, "data/test/transactions.jsonl"))}
        labels = jsonl(os.path.join(ROOT, "data/test/adversarial.jsonl"))
        self.assertGreaterEqual(sum(x["kind"] != "benign_lookalike" for x in labels), 15)
        for x in labels:
            self.assertEqual(txns[x["txn_id"]]["tenant_id"], x["tenant_id"])

    def test_test_split_eval_passes(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "run_eval.py"), "--data",
                            os.path.join(ROOT, "data", "test")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-800:])


class LifecycleChecks(unittest.TestCase):
    def log(self, refund):
        return [{"name": "get_transaction", "input": {"txn_id": "txn_X"},
                 "result": {"status": "found", "source": "txn_db",
                            "data": {"txn_id": "txn_X", "status": "approved", "amount": "100.00", "refund": refund}}}]

    def test_pending_refund_is_not_a_completed_refund(self):
        log = self.log({"amount": "100.00", "status": "pending"})
        self.assertIn("refund_status_mismatch:record=pending", evidence_check("It was refunded.", log, "txn_X").reasons)
        self.assertEqual(evidence_check("A refund of 100.00 is pending.", log, "txn_X").action, "allow")

    def test_partial_refund_is_not_full(self):
        log = self.log({"amount": "50.00", "status": "succeeded"})
        self.assertIn("refund_amount_mismatch:partial", evidence_check("It was fully refunded.", log, "txn_X").reasons)

    def test_relative_dates_are_not_from_records(self):
        log = self.log({"amount": "100.00", "status": "succeeded", "at": "2026-11-24T09:00:00Z"})
        self.assertIn("unverifiable_relative_date", evidence_check("It was refunded yesterday.", log, "txn_X").reasons)
        self.assertEqual(evidence_check("It was refunded on 2026-11-24.", log, "txn_X").action, "allow")
        self.assertEqual(evidence_check("It was refunded on 24 November 2026.", log, "txn_X").action, "allow")
        wrong_day = evidence_check("It was refunded on 2026-11-25.", log, "txn_X")
        self.assertIn("ungrounded_date:2026-11-25", wrong_day.reasons)


if __name__ == "__main__":
    unittest.main()
