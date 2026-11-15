"""One place that writes audit records: JSON lines to a file, or to stdout.

    path "-"     stdout (containers: the log collector ships it; pods have a read-only filesystem)
    path "x.jsonl"  appended to that file (single host, tests)
    path None/""  off

Every record carries a "stream" field (turn, control, guard, tickets) so one log
destination can hold all of them and still be filtered.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def write(path: str | None, stream: str, record: dict[str, Any]) -> None:
    if not path:
        return
    line = json.dumps({"stream": stream, **record}) + "\n"
    if path == "-":
        sys.stdout.write(line)
        sys.stdout.flush()
        return
    with open(path, "a") as f:
        f.write(line)


def sibling(path: str | None, suffix: str) -> str | None:
    """Where a secondary audit stream goes: next to the main file, or stdout too."""
    if not path:
        return None
    return path if path == "-" else path + suffix
