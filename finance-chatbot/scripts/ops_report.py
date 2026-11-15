"""Cost of strictness, from the turn audit log: repair, record-only, refusal and handoff
rates, model calls per turn, latency.

  python scripts/ops_report.py audit.jsonl
  python scripts/ops_report.py --demo        # run the held-out eval offline, then report
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.evalkit import load_cases  # noqa: E402
from finbot.opsmetrics import summarise  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "..", "data")


def demo(hallucinate: bool, **settings) -> dict:
    """The held-out questions through the offline bot; with hallucinate, half of the model's
    answers carry an injected error, so the repair and fallback paths are exercised."""
    from finbot.faults import VariantLLM
    with tempfile.TemporaryDirectory() as tmp:
        audit = os.path.join(tmp, "audit.jsonl")
        s = Settings(data_dir=os.path.join(DATA, "test"), kb_path="/nonexistent", **settings)
        for i, case in enumerate(load_cases(os.path.join(DATA, "test", "eval.jsonl"))):
            bot = build(s, audit_path=audit)
            if hallucinate:
                bot.llm = VariantLLM(bot.llm, seed=i)
            cs = bot.new_session(case["tenant_id"], case["groups"], case.get("role", "support"))
            for q in case["turns"]:
                bot.ask(cs, q)
        return summarise(audit)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audit", nargs="?")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        rows = [("clean model", {"hallucinate": False}),
                ("faulty model, repair on", {"hallucinate": True}),
                ("faulty model, no repair, refuse", {"hallucinate": True, "repair_attempts": 0,
                                                    "record_fallback": False}),
                ("faulty model, no repair, record answer", {"hallucinate": True, "repair_attempts": 0})]
        print(f"{'run':<40} {'answered':>8} {'repaired':>8} {'record':>7} {'refused':>8} {'calls/turn':>10}")
        for name, kw in rows:
            m = demo(**kw)
            print(f"{name:<40} {m['answered_rate']:>8.1%} {m['repair_rate']:>8.1%} {m['record_only_rate']:>7.1%} "
                  f"{m['refusal_rate']:>8.1%} {m['model_calls_per_model_turn']:>10}")
    elif a.audit:
        print(json.dumps(summarise(a.audit), indent=1))
    else:
        ap.error("give an audit log path, or --demo")


if __name__ == "__main__":
    main()
