"""Consistency eval: pass^k (Yao et al. 2024, tau-bench, arXiv 2406.12045).

Each labelled case runs k times against a stand-in model that varies run to run
(VariantLLM: half of its answers carry a random hallucination). A case passes^k only
if ALL k runs pass the case's checks and no injected error reaches the user. A bot that
is right once but not every time scores well on pass^1 and badly on pass^k.

  python scripts/run_consistency.py                  # dev set, k=8
  python scripts/run_consistency.py --data data/test --k 4
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.evalkit import load_cases, score  # noqa: E402
from finbot.faults import VariantLLM  # noqa: E402


def stable_seed(case_id: str, run: int) -> int:
    """Same seed in every process (hash() is randomised per process), so a failure reproduces."""
    return int(hashlib.sha256(f"{case_id}:{run}".encode()).hexdigest()[:8], 16)


def run(data: str, k: int, p: float = 0.5) -> dict:
    s = Settings(data_dir=data, kb_path="/nonexistent", llm_provider="fake", guardrail_provider="local")
    results: dict[str, list[bool]] = {}
    category: dict[str, str] = {}
    for case in load_cases(os.path.join(data, "eval.jsonl")):
        category[case["id"]] = case["category"]
        runs = []
        for seed in range(k):
            b = build(s)
            b.sleep = b.toolbox.sleep = lambda _s: None
            model = b.llm = VariantLLM(b.llm, seed=stable_seed(case["id"], seed), p=p)
            cs = b.new_session(case["tenant_id"], case["groups"], case.get("role", "support"))
            turns, leaked = [], False
            for q in case["turns"]:
                n = len(model.markers)
                t = b.ask(cs, q)
                # an injected error counts only against the turn it was injected into: "was approved"
                # planted in turn 1 is not a leak when turn 2 is about an approved payment
                leaked |= any(m in t.answer for m in model.markers[n:])
                turns.append(t)
            runs.append(not score(case, turns, cs) and not leaked)
        results[case["id"]] = runs
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for cid, runs in results.items():
        by_cat[category[cid]][0] += all(runs)
        by_cat[category[cid]][1] += 1
    n = len(results)
    return {"k": k, "cases": n,
            "pass_1": round(sum(sum(r) / k for r in results.values()) / n, 4),
            "pass_k": round(sum(all(r) for r in results.values()) / n, 4),
            "by_category": dict(by_cat),
            "inconsistent": sorted(c for c, r in results.items() if any(r) and not all(r)),
            "always_failing": sorted(c for c, r in results.items() if not any(r))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--p", type=float, default=0.5, help="share of answers carrying a hallucination")
    ap.add_argument("--min", type=float, default=1.0, help="fail below this pass^k")
    a = ap.parse_args()
    r = run(a.data, a.k, a.p)
    print(f"{r['cases']} cases x k={a.k} runs, {int(a.p * 100)}% of answers corrupted")
    print(f"pass^1 = {r['pass_1']:.3f}   pass^{a.k} = {r['pass_k']:.3f}")
    for cat, (ok, n) in sorted(r["by_category"].items()):
        print(f"  {cat:<20} {ok}/{n}")
    for label in ("inconsistent", "always_failing"):
        if r[label]:
            print(f"{label}: {', '.join(r[label])}")
    sys.exit(0 if r["pass_k"] >= a.min else 1)


if __name__ == "__main__":
    main()
