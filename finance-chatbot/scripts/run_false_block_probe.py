"""False-block probe: does the gate ever stop a correct answer?

Every failed or declined payment and every domain record is asked about by both roles
(support and customer), through the whole turn pipeline, with the clean scripted model.
A turn counts as a false block if the gate stopped the model's first answer: the user saw a
refusal, a record-built answer, or a repaired answer. Each stopped turn is printed with its
reasons. This is the probe behind the paper's "correct answers wrongly blocked" row.

  python3 scripts/run_false_block_probe.py                    # data and data/test
  python3 scripts/run_false_block_probe.py --data data/test --out probe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.domains import DOMAINS  # noqa: E402

ROLES = ("support", "customer")
GATE_PREFIXES = ("repaired:", "records_only:")


def rows(d: str, f: str) -> list[dict]:
    path = os.path.join(d, f"{f}.jsonl")
    return [json.loads(line) for line in open(path)] if os.path.exists(path) else []


def questions(d: str) -> list[tuple[str, str, str]]:
    """(kind, tenant, question) for every failed payment and every domain record."""
    out = [("payments", r["tenant_id"], f"Why did {r['txn_id']} fail?") for r in rows(d, "transactions")
           if r.get("status") != "approved"]
    for dom in DOMAINS.values():
        for r in rows(d, dom.name):
            if isinstance(rid := r.get(dom.id_arg), str):
                out.append((dom.name, r["tenant_id"], f"What is the status of {rid}?"))
    return out


def probe(d: str, configure=None) -> dict:
    s = Settings(data_dir=d, kb_path=os.environ.get("FINBOT_KB_PATH", "/nonexistent"))
    bot = build(s)
    if configure:
        configure(bot)
    by_kind: dict[str, list[int]] = {}
    stopped = []
    for kind, tenant, q in questions(d):
        for role in ROLES:
            t = bot.ask(bot.new_session(tenant, ["support"], role), q)
            gate = [r for r in t.reasons if str(r).startswith(GATE_PREFIXES)]
            hit = bool(t.blocked_by) or bool(gate)
            c = by_kind.setdefault(kind, [0, 0])
            c[0] += 1
            c[1] += hit
            if hit:
                stopped.append({"kind": kind, "role": role, "question": q, "blocked_by": t.blocked_by,
                                "reasons": t.reasons, "answer": t.answer[:300]})
    return {"data": d, "by_kind": by_kind, "asked": sum(c[0] for c in by_kind.values()),
            "stopped": stopped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", action="append", help="data directory (repeatable)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    results, asked, stopped = [], 0, 0
    for d in a.data or ["data", "data/test"]:
        r = probe(d)
        results.append(r)
        asked += r["asked"]
        stopped += len(r["stopped"])
        print(f"{d}: {len(r['stopped'])} of {r['asked']} correct answers stopped")
        for kind, (n, hit) in sorted(r["by_kind"].items()):
            print(f"  {kind:<14} {hit:>4} of {n}")
        for x in r["stopped"][:20]:
            print(f"  STOPPED {x['role']:<8} {x['question']}  {x['blocked_by'] or ''} {x['reasons']}")
    print(f"\ncorrect answers stopped by the gate: {stopped} of {asked}")
    if a.out:
        with open(a.out, "w") as f:
            json.dump(results, f, indent=1)
    return 1 if stopped else 0


if __name__ == "__main__":
    raise SystemExit(main())
