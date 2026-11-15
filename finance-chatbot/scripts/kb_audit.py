"""Check the help pages before the bot repeats them: pages past the age limit, and
decline-code sections that disagree with the retry policy and code tables.

  python scripts/kb_audit.py                 # data/kb_pages.json
  python scripts/kb_audit.py pages.json --max-age-days 365
Exit code 1 when there are findings, so it can gate an ingestion job.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.freshness import audit_pages  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="?", default=os.path.join(os.path.dirname(__file__), "..", "data", "kb_pages.json"))
    ap.add_argument("--max-age-days", type=int, default=540)
    a = ap.parse_args()
    with open(a.pages) as f:
        findings = audit_pages(json.load(f), a.max_age_days, date.today())
    for x in findings:
        print(f"{x['kind']:<17} {x['page']}: {x['detail']}")
    print(f"{len(findings)} finding(s)")
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
