"""Reconciliation jobs (finbot/reconcile.py). Read-only; exits 1 when it finds a problem.

    python3 scripts/reconcile.py tickets                       # resolved tickets vs systems of record
    python3 scripts/reconcile.py audit --audit audit.jsonl --tenant m_100     # turn audit log vs committed turns
    python3 scripts/reconcile.py index --source local:data/kb_pages.json --store local:kb_vectors.json

Settings (store, records, tickets) come from the usual FINBOT_* environment variables.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.config import Settings  # noqa: E402
from finbot.container import Container  # noqa: E402
from finbot.reconcile import reconcile_audit, reconcile_index, reconcile_tickets, summary, toolbox_lookup  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jobs", nargs="+", choices=["tickets", "audit", "index"])
    ap.add_argument("--audit", default="", help="turn audit log (JSON lines) for the audit job")
    ap.add_argument("--source", default="", help="help-page source spec for the index job")
    ap.add_argument("--store", default="", help="vector store spec for the index job")
    ap.add_argument("--tenant", help="operator-authorized tenant for the audit job (required)")
    a = ap.parse_args()
    if "audit" in a.jobs and (not a.tenant or not a.audit):
        ap.error("audit requires --audit and --tenant; tenant authority is never inferred from logs")
    c = Container(Settings())
    reports = []
    try:
        if "tickets" in a.jobs:
            reports.append(reconcile_tickets(c.tickets, toolbox_lookup(c.toolbox), audit_path=c.audit_path))
        if "audit" in a.jobs:
            with open(a.audit) as f:
                reports.append(reconcile_audit(f, c.store, tenant_id=a.tenant))
        if "index" in a.jobs:
            from finbot.ingestion import IngestionPipeline, source_from, store_from
            src, store = source_from(a.source), store_from(a.store)
            reports.append(reconcile_index(src, store, IngestionPipeline(src, None, None, store).screen))
    finally:
        c.close()
    out = summary(reports)
    print(json.dumps(out, indent=2))
    return 1 if out["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
