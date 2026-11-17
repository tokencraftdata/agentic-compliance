"""Multi-record probe for claim binding (finbot/binding.py).

For pairs of records of the same tenant with different statuses, it asks the gate about a
correct two-record answer and about the same answer with the two statuses swapped. A correct
answer must pass; a swapped one must be blocked.

  python3 scripts/run_binding_probe.py                 # data and data/test
  python3 scripts/run_binding_probe.py --data /tmp/finbot_test --pairs 100
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.domains import DOMAINS  # noqa: E402
from finbot.tools import envelope  # noqa: E402

AMOUNT_FIELDS = ("amount", "principal", "balance", "claim_amount", "total", "price")


def rows(d: str, f: str) -> list[dict]:
    path = os.path.join(d, f"{f}.jsonl")
    return [json.loads(line) for line in open(path)] if os.path.exists(path) else []


def call(tool: str, key: str, r: dict) -> dict:
    return {"name": tool, "input": {key: r[key]},
            "result": envelope("found", "db", {k: v for k, v in r.items() if k != "tenant_id"})}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", action="append", help="data directory (repeatable)")
    ap.add_argument("--pairs", type=int, default=60, help="pairs per record type")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    chain = build().gate.chain
    rng = random.Random(a.seed)
    specs = [("transactions", "get_transaction", "txn_id")] + [(d.name, d.tool, d.id_arg) for d in DOMAINS.values()]
    total = {"correct": 0, "blocked": 0, "swapped": 0, "allowed": 0}
    print(f"{'data':<12} {'records':<14} {'pairs':>5} {'correct blocked':>16} {'swaps allowed':>14}")
    for d in a.data or ["data", "data/test"]:
        for name, tool, key in specs:
            rs = rows(d, name)
            if not rs:
                continue
            amt = next((k for k in AMOUNT_FIELDS if k in rs[0]), None)
            pairs = [(x, y) for x, y in itertools.combinations(rs, 2) if x["tenant_id"] == y["tenant_id"]
                     and x.get("status") != y.get("status") and (not amt or x.get(amt) != y.get(amt))]
            rng.shuffle(pairs)
            pairs = pairs[:a.pairs]
            if not pairs:
                continue
            pre, verb = ("Payment ", "was") if name == "transactions" else ("", "is")

            def say(r, status, key=key, amt=amt, pre=pre, verb=verb):
                money = f", for {r[amt]} {r.get('currency', 'USD')}" if amt and r.get(amt) not in (None, "") else ""
                return f"{pre}{r[key]} {verb} {str(status).replace('_', ' ')}{money}."
            blocked = allowed = 0
            for x, y in pairs:
                log = [call(tool, key, x), call(tool, key, y)]
                if chain.check(f"{say(x, x['status'])} {say(y, y['status'])}", log, None).action == "block":
                    blocked += 1
                if chain.check(f"{say(x, y['status'])} {say(y, x['status'])}", log, None).action == "allow":
                    allowed += 1
            total["correct"] += len(pairs)
            total["swapped"] += len(pairs)
            total["blocked"] += blocked
            total["allowed"] += allowed
            print(f"{os.path.basename(d.rstrip('/')):<12} {name:<14} {len(pairs):>5} {blocked:>16} {allowed:>14}")
    print(f"\ncorrect two-record answers blocked: {total['blocked']} of {total['correct']}")
    print(f"swapped-status answers allowed:     {total['allowed']} of {total['swapped']}")
    return 1 if total["blocked"] or total["allowed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
