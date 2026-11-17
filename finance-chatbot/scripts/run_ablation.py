"""Offline baselines and ablations: what each part of the system adds.

Every configuration runs on the same tools, data and cases:

  full                  the system as shipped
  no_gate               no hallucination gate: the model's answer is shown as written
                        (the offline stand-in for "prompting alone")
  template_only         no model text at all: every record answer is built by code from the
                        record (recordanswer.py); anything else is refused
  no_repair             a failed draft goes straight to the record-built fallback
  no_fallback           one repair, then refuse
  no_repair_no_fallback a failed draft is refused
  no_domain, no_binding, no_reason   one verifier removed

For each it reports, per data set:
  eval      labelled eval cases passed with the clean scripted model
  stopped   correct answers the gate stopped (scripts/run_false_block_probe.py)
  leaked    randomized, record-derived hallucinations that reached the user (finbot/faultgen.py),
            with repairs repeating the mistake half the time
  repaired / records_only / refused   what the user saw instead, as a share of corrupted turns

  python3 scripts/run_ablation.py                       # data and data/test, seeds 101 102 103
  python3 scripts/run_ablation.py --configs full no_gate --out ablation.json

The fault model is scripted. These numbers say what each component does to injected errors;
they say nothing about how often a live model makes them. Baselines that need a live service
(Bedrock contextual grounding, an LLM verifier such as MiniCheck or a Claude judge, a live
model with and without the gate) are not here: they need credentials and budget.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.evalkit import load_cases, run_case  # noqa: E402
from finbot.verification import (  # noqa: E402
    BindingVerifier,
    DomainVerifier,
    GateResult,
    ReasonVerifier,
    VerifierChain,
)

sys.path.insert(0, os.path.dirname(__file__))
from run_false_block_probe import probe  # noqa: E402
from run_stress import stress, total  # noqa: E402


class TemplateGate:
    """Ignores the model's text: the answer is the one code builds from the records, verified
    like any other; with no buildable answer, the turn is refused."""

    def __init__(self, chain):
        self.chain = chain

    def run(self, answer, tool_log, focus, regenerate, fallback=None):
        log = list(tool_log)
        built = fallback(log) if fallback else None
        if built and self.chain.check(built, log, focus()).action == "allow":
            # Marked like a record-built answer so the scoring can tell it from model text.
            return GateResult("allow", built, ["records_only:template"], records_only=True, tool_log=log)
        g = self.chain.check(answer, log, focus())
        return GateResult("block", g.text if g.action == "block" else "I won't guess.", ["template_unavailable"],
                          tool_log=log)


def _settings(bot, **kw):
    bot.settings = dataclasses.replace(bot.settings, **kw)


def _drop(bot, cls):
    bot.gate.chain = VerifierChain([v for v in bot.gate.chain.verifiers if not isinstance(v, cls)])


CONFIGS = {
    "full": lambda b: None,
    "no_gate": lambda b: setattr(b.gate, "chain", VerifierChain([])),
    "template_only": lambda b: setattr(b, "gate", TemplateGate(b.gate.chain)),
    "no_repair": lambda b: setattr(b.gate, "repair_attempts", 0),
    "no_fallback": lambda b: _settings(b, record_fallback=False),
    "no_repair_no_fallback": lambda b: (setattr(b.gate, "repair_attempts", 0), _settings(b, record_fallback=False)),
    "no_domain": lambda b: _drop(b, DomainVerifier),
    "no_binding": lambda b: _drop(b, BindingVerifier),
    "no_reason": lambda b: _drop(b, ReasonVerifier),
}


def run(config: str, data: str, seeds: list[int], persist: float) -> dict:
    s = Settings(data_dir=data, kb_path=os.environ.get("FINBOT_KB_PATH", "/nonexistent"))
    configure = CONFIGS[config]

    def factory():
        b = build(s)
        configure(b)
        return b

    cases = load_cases(os.path.join(data, "eval.jsonl"))
    passed = sum(not run_case(factory, c)[0] for c in cases)
    # Template-only answers are all built by code, which the probe counts as "stopped": not meaningful there.
    fb = probe(data, configure) if config != "template_only" else {"stopped": [], "asked": 0}
    agg = None
    for seed in seeds:
        t = total(stress(s, cases, seed, persist, configure=configure))
        agg = t if agg is None else dataclasses.replace(agg, **{k: getattr(agg, k) + v for k, v in t.row().items()})
    inj = agg.injected or 1
    return {"config": config, "data": data, "eval_passed": passed, "eval_total": len(cases),
            "stopped": len(fb["stopped"]), "asked": fb["asked"], **agg.row(),
            "leak_rate": agg.leaked / inj, "refusal_rate": agg.refused / inj,
            "records_only_rate": agg.records_only / inj, "repaired_rate": agg.repaired / inj}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", action="append")
    ap.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    ap.add_argument("--seeds", type=int, nargs="+", default=[101, 102, 103])
    ap.add_argument("--persist", type=float, default=0.5)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    rows = []
    hdr = f"{'config':<22} {'data':<10} {'eval':>9} {'stopped':>10} {'injected':>9} {'leaked':>8} " \
          f"{'repaired':>9} {'records':>8} {'refused':>8}"
    for d in a.data or ["data", "data/test"]:
        print(f"\n{hdr}")
        for c in a.configs:
            r = run(c, d, a.seeds, a.persist)
            rows.append(r)
            stopped = f"{r['stopped']}/{r['asked']}" if r["asked"] else "n/a"
            print(f"{c:<22} {os.path.basename(d.rstrip('/')):<10} {r['eval_passed']:>4}/{r['eval_total']:<4} "
                  f"{stopped:>10} {r['injected']:>9} {r['leak_rate']:>8.1%} "
                  f"{r['repaired_rate']:>9.1%} {r['records_only_rate']:>8.1%} {r['refusal_rate']:>8.1%}", flush=True)
    if a.out:
        with open(a.out, "w") as f:
            json.dump({"seeds": a.seeds, "persist": a.persist, "rows": rows}, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
