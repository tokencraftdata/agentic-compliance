"""False-block probe: every correct answer finbot can give must get through.

Asks about every failed payment and every domain record in a data set, as both roles (customer
and support), with the scripted model, and counts answers the checks wrongly blocked.

    python3 scripts/false_block_probe.py data data/test      # exit 1 if any answer is blocked
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.config import Settings  # noqa: E402
from finbot.container import Container  # noqa: E402
from finbot.domains import DOMAINS  # noqa: E402


def questions(data_dir: str) -> list[tuple[str, str]]:
    qs = []
    with open(os.path.join(data_dir, "transactions.jsonl")) as f:
        for t in (json.loads(line) for line in f):
            if t["status"] != "approved":
                qs.append((t["tenant_id"], f"why did {t['txn_id']} fail?"))
                qs.append((t["tenant_id"], f"what should I do about {t['txn_id']}? any processor or routing issue?"))
    for name, d in DOMAINS.items():
        path = os.path.join(data_dir, f"{name}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for r in (json.loads(line) for line in f):
                qs.append((r["tenant_id"], f"what is the status of {r[d.id_arg]}?"))
                qs.append((r["tenant_id"], f"why, and what should I do next about {r[d.id_arg]}?"))
    return qs


def run(data_dir: str) -> tuple[int, int]:
    asked = blocked = 0
    qs = questions(data_dir)
    for role in ("customer", "support"):
        c = Container(Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent",
                               data_dir=data_dir, store_path=":memory:"))
        try:
            bot = c.chatbot
            for tenant, q in qs:
                t = bot.ask(bot.new_session(tenant, ["support"], role), q)
                asked += 1
                if t.blocked_by:
                    blocked += 1
                    print("BLOCKED", role, q, t.reasons[:3])
        finally:
            c.close()
    return asked, blocked


def main(dirs: list[str]) -> int:
    total_a = total_b = 0
    for d in dirs or ["data", "data/test"]:
        a, b = run(d)
        print(d, a, "asked", b, "blocked")
        total_a, total_b = total_a + a, total_b + b
    print("TOTAL", total_a, "asked", total_b, "blocked")
    return 1 if total_b else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
