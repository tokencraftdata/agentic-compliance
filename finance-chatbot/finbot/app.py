"""CLI entry point: python -m finbot.app  (demo data, fake LLM by default).

Real run:
  FINBOT_LLM_PROVIDER=bedrock FINBOT_GUARDRAIL=bedrock FINBOT_GUARDRAIL_ID=... python -m finbot.app
"""
from __future__ import annotations

import json
import os

from .config import Settings
from .orchestrator import Chatbot


def _jsonl(path: str) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_dataset(data_dir: str) -> tuple[list[dict], list[dict], list[dict]] | None:
    """Load the synthetic dataset from scripts/generate_synthetic.py, if present."""
    need = ("transactions.jsonl", "logs.jsonl", "kb_pages.json")
    if not data_dir or not all(os.path.exists(os.path.join(data_dir, n)) for n in need):
        return None
    with open(os.path.join(data_dir, "kb_pages.json")) as f:
        pages = json.load(f)
    return (_jsonl(os.path.join(data_dir, "transactions.jsonl")),
            _jsonl(os.path.join(data_dir, "logs.jsonl")), pages)


def load_records(data_dir: str) -> dict[str, list[dict]] | None:
    """Domain plug-in records (accounts.jsonl, transfers.jsonl, kyc.jsonl, lending.jsonl), if present."""
    from .domains import DOMAINS
    out = {name: _jsonl(os.path.join(data_dir, f"{name}.jsonl")) for name in DOMAINS
           if data_dir and os.path.exists(os.path.join(data_dir, f"{name}.jsonl"))}
    return out or None


def build(settings: Settings | None = None, audit_path: str | None = None) -> Chatbot:
    """The orchestration layer, wired by the composition root (finbot/container.py)."""
    from .container import Container
    return Container(settings, audit_path=audit_path).chatbot


def main() -> None:
    """Chat through a durable session. Kill the process and run again with
    FINBOT_SESSION=<id> to resume from the last committed turn."""
    import uuid

    from .runtime import Worker
    from .store import SessionStore

    bot = build(audit_path="audit.jsonl")
    store = SessionStore(os.environ.get("FINBOT_STORE", "sessions.db"))
    worker = Worker(bot, store, lease_s=5)
    tenant = os.environ.get("FINBOT_TENANT", "m_100")
    sid = os.environ.get("FINBOT_SESSION") or worker.start_session(tenant, ["support"])
    samples = [next(iter(rows)) for rows in getattr(bot.toolbox.records, "rows", {}).values() if rows][:2]
    print(f"finbot ready, session {sid} (Ctrl-D to quit). Try: what is the status of "
          f"{' or '.join(samples) or 'acct_...'}?")
    while True:
        try:
            q = input("> ").strip()
        except EOFError:
            break
        if q == "/status":  # model engine health: breaker state, failovers, latency per target
            for row in bot.engine().status():
                print("  " + "  ".join(f"{k}={v}" for k, v in row.items()))
            continue
        if q:
            r = worker.handle(sid, uuid.uuid4().hex, q)
            tag = f"  [{r['blocked_by']}: {', '.join(r['reasons'])}]" if r.get("blocked_by") else ""
            print(r["answer"] + tag)
    store.release(sid, worker.id)


if __name__ == "__main__":
    main()
