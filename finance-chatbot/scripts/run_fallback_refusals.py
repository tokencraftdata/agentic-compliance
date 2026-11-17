"""How much the record-built fallback cuts refusals, with repair switched off.

The paper's Section 3 says the fallback "reduced refusals from 39.9% to 8.9% of turns" with a
faulty scripted model and repair off, but no script produced that number. This one does the
measurement in a fixed, documented way: the legacy VariantLLM (half of all answers carry one
of the twelve fixed hallucinations, run_consistency.py's model), repair_attempts=0, every eval
case under 16 seeds, with the fallback off and on. A refusal is a turn the evidence gate
withheld. For the same measurement with randomized, record-derived faults and repairs that can
fail, see scripts/run_ablation.py (no_repair vs no_repair_no_fallback).

  python3 scripts/run_fallback_refusals.py
  python3 scripts/run_fallback_refusals.py --data data/test --seeds 32
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.evalkit import load_cases  # noqa: E402
from finbot.faults import VariantLLM  # noqa: E402


def refusals(s: Settings, cases: list[dict], seeds: int, p: float) -> tuple[int, int]:
    turns = refused = 0
    for seed in range(seeds):
        for i, c in enumerate(cases):
            b = build(s)
            b.llm = VariantLLM(b.llm, seed=seed * 1000 + i, p=p)
            cs = b.new_session(c["tenant_id"], c["groups"], c.get("role", "support"))
            for q in c["turns"]:
                turns += 1
                refused += b.ask(cs, q).blocked_by == "evidence_guard"
    return refused, turns


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", action="append")
    ap.add_argument("--seeds", type=int, default=16)
    ap.add_argument("--p", type=float, default=0.5, help="share of answers corrupted")
    a = ap.parse_args()
    print(f"{'data':<10} {'fallback':>8} {'refused':>9} {'turns':>7} {'share':>7}")
    for d in a.data or ["data", "data/test"]:
        s = Settings(data_dir=d, kb_path=os.environ.get("FINBOT_KB_PATH", "/nonexistent"), repair_attempts=0)
        cases = load_cases(os.path.join(d, "eval.jsonl"))
        for fb in (False, True):
            r, n = refusals(dataclasses.replace(s, record_fallback=fb), cases, a.seeds, a.p)
            print(f"{os.path.basename(d.rstrip('/')):<10} {'on' if fb else 'off':>8} {r:>9} {n:>7} {r / n:>7.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
