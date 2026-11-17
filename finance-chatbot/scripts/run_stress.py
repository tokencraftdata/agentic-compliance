"""Randomized hallucination stress test (finbot/faultgen.py).

  python3 scripts/run_stress.py                          # dev set, seed 1, repairs fail half the time
  python3 scripts/run_stress.py --data data/test --seeds 1 2 3 --persist 0.5 --out stress.json

Unlike `run_eval.py --hallucinate` (twelve fixed strings, repairs always clean), every value
here is derived from the turn's own records and drawn from a seeded RNG, wording comes from
phrase banks that include phrasings the checks don't list, and with probability --persist the
repair repeats the mistake, so the record-built fallback and the refusal path are both used.

Columns: injected (first drafts corrupted), persisted (repairs corrupted again), and what the
user saw: a repaired model answer, a record-built answer, a refusal, or the corruption (LEAKED).
Exit status 1 if anything leaked. Leaks are printed with the answer so they can be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.evalkit import load_cases  # noqa: E402
from finbot.faultgen import FAMILIES, FamilyStats, RecordFaultLLM, score_turn  # noqa: E402


def stress(s: Settings, cases: list[dict], seed: int, persist: float, families=FAMILIES,
           configure=None) -> dict[str, FamilyStats]:
    """One pass over the cases. `configure(bot)` may switch parts of the system off (ablations)."""
    stats: dict[str, FamilyStats] = {}
    for n, case in enumerate(cases):
        bot = build(s)
        if configure:
            configure(bot)
        bot.llm = model = RecordFaultLLM(bot.llm, seed=seed * 100_003 + n, families=families, persist=persist)
        cs = bot.new_session(case["tenant_id"], case["groups"], case.get("role", "support"))
        for q in case["turns"]:
            k = len(model.injections)
            t = bot.ask(cs, q)
            score_turn(t, model.injections[k:], stats)
    return stats


def total(stats: dict[str, FamilyStats]) -> FamilyStats:
    out = FamilyStats()
    for s in stats.values():
        for k, v in s.row().items():
            setattr(out, k, getattr(out, k) + v)
    return out


def table(stats: dict[str, FamilyStats]) -> str:
    cols = ("injected", "persisted", "repaired", "records_only", "refused", "leaked")
    lines = [f"{'family':<19}" + "".join(f"{c:>13}" for c in cols)]
    for fam in [f for f in FAMILIES if f in stats] + ["TOTAL"]:
        r = (total(stats) if fam == "TOTAL" else stats[fam]).row()
        lines.append(f"{fam:<19}" + "".join(f"{r[c]:>13}" for c in cols))
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--seeds", type=int, nargs="+", default=[1])
    p.add_argument("--persist", type=float, default=0.5, help="chance a repair repeats the mistake")
    p.add_argument("--families", nargs="+", default=list(FAMILIES), choices=FAMILIES)
    p.add_argument("--out", default="")
    a = p.parse_args()
    s = Settings(data_dir=a.data, kb_path=os.environ.get("FINBOT_KB_PATH", "/nonexistent"))
    cases = load_cases(os.path.join(a.data, "eval.jsonl"))
    report, leaks = {}, 0
    for seed in a.seeds:
        stats = stress(s, cases, seed, a.persist, tuple(a.families))
        print(f"\nseed {seed}, persist {a.persist}, data {a.data}\n{table(stats)}")
        for fam, st in stats.items():
            for ex in st.examples:
                print(f"  LEAK {fam}: {ex}")
        leaks += total(stats).leaked
        report[seed] = {fam: {**st.row(), "examples": st.examples} for fam, st in stats.items()}
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"data": a.data, "persist": a.persist, "seeds": report}, f, indent=1)
    sys.exit(1 if leaks else 0)


if __name__ == "__main__":
    main()
