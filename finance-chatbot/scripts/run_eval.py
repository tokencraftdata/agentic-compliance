"""Run the labelled eval set through finbot and score each case.

  python3 scripts/run_eval.py                       # offline: fake LLM + local guardrail
  FINBOT_LLM_PROVIDER=bedrock FINBOT_GUARDRAIL=bedrock FINBOT_GUARDRAIL_ID=... \
    python3 scripts/run_eval.py --out eval_report.json   # live (costs model usage)

Checks are deterministic string/tool/state checks, no LLM judge. The offline
run validates the pipeline wiring; only a live run measures model behaviour.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.evalkit import load_cases, run_case  # noqa: E402


def hallucination_stress(s: Settings, cases: list[dict]) -> int:
    """Inject a known hallucination into each answer; count how many reach the user.

    Works with the live model too: the corruption is added on top of Claude's
    real answer, so it measures the guards on realistic text."""
    from finbot.faults import MODES, HallucinatingLLM
    leaks = 0
    print(f"{'mode':<17} {'injected':>8} {'repaired':>8} {'fallback':>8} {'LEAKED':>7}")
    for mode in MODES:
        injected = repaired = fallback = leaked = 0
        for case in cases:
            bot = build(s)
            bot.llm = HallucinatingLLM(bot.llm, mode)
            cs = bot.new_session(case["tenant_id"], case["groups"], case.get("role", "support"))
            for q in case["turns"]:
                n = len(bot.llm.markers)
                t = bot.ask(cs, q)
                new = bot.llm.markers[n:]
                if not new:
                    continue  # blocked at input; nothing injected
                injected += 1
                if any(m in t.answer for m in new):
                    leaked += 1
                elif t.repaired:
                    repaired += 1
                else:
                    fallback += 1
        leaks += leaked
        print(f"{mode:<17} {injected:>8} {repaired:>8} {fallback:>8} {leaked:>7}")
    return leaks


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="data")
    p.add_argument("--out", default="")
    p.add_argument("--hallucinate", action="store_true", help="run the hallucination stress test instead")
    a = p.parse_args()
    s = Settings(data_dir=a.data, kb_path=os.environ.get("FINBOT_KB_PATH", "/nonexistent"))
    cases = load_cases(os.path.join(a.data, "eval.jsonl"))
    if a.hallucinate:
        sys.exit(1 if hallucination_stress(s, cases) else 0)

    results, by_cat = [], defaultdict(lambda: [0, 0])
    for case in cases:
        fails, turns, _ = run_case(lambda: build(s), case)
        by_cat[case["category"]][0] += not fails
        by_cat[case["category"]][1] += 1
        results.append({"id": case["id"], "category": case["category"], "pass": not fails,
                        "failures": fails, "answer": turns[-1].answer, "blocked_by": turns[-1].blocked_by})
        print(f"{'PASS' if not fails else 'FAIL'}  {case['id']:<22} {'; '.join(fails)}")

    passed = sum(r["pass"] for r in results)
    print(f"\n{passed}/{len(results)} passed  (provider={s.llm_provider}, guardrail={s.guardrail_provider})")
    for cat, (ok, n) in sorted(by_cat.items()):
        print(f"  {cat:<20} {ok}/{n}")
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"provider": s.llm_provider, "model": s.resolved_model(), "guardrail": s.guardrail_provider,
                       "passed": passed, "total": len(results), "results": results}, f, indent=1)
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
