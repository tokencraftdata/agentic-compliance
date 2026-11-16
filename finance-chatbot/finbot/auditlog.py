"""One place that writes audit records: JSON lines to a file, or to stdout.

    path "-"     stdout (containers: the log collector ships it; pods have a read-only filesystem)
    path "x.jsonl"  appended to that file (single host, tests)
    path None/""  off

Every record carries a "stream" field (turn, control, guard, tickets, dlp) so one log
destination can hold all of them and still be filtered.

Tamper evidence: records are hash-chained per writer. Each record carries
    chain  this writer's id (a process; several pods write to one destination)
    seq    position in the chain
    prev   the previous record's hash
    hash   SHA-256 over prev + the record, or HMAC-SHA256 with FINBOT_AUDIT_KEY when set
Deleting, reordering or editing a record breaks the chain, and `verify` names the first
bad line. Without the key, someone who can rewrite the whole file can recompute the
chain; with it, they also need the key. Ship the log to write-once storage (S3 Object
Lock in compliance mode) so it cannot be rewritten at all.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import threading
import uuid
from typing import Any

GENESIS = "0" * 64
_state: dict[str | None, tuple[str, int, str]] = {}   # path -> (chain id, seq, last hash)
_lock = threading.Lock()
_key: bytes | None = None


def configure(key: str | None) -> None:
    """Set the HMAC key for new records (FINBOT_AUDIT_KEY); None or "" means plain SHA-256."""
    global _key
    _key = key.encode() if key else None


def _digest(prev: str, body: str, key: bytes | None) -> str:
    data = (prev + body).encode()
    return hmac.new(key, data, hashlib.sha256).hexdigest() if key else hashlib.sha256(data).hexdigest()


def write(path: str | None, stream: str, record: dict[str, Any]) -> None:
    if not path:
        return
    with _lock:
        chain, seq, prev = _state.get(path) or (uuid.uuid4().hex[:12], 0, GENESIS)
        rec = {"stream": stream, **record, "chain": chain, "seq": seq + 1, "prev": prev}
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)
        rec["hash"] = _digest(prev, body, _key)
        _state[path] = (chain, seq + 1, rec["hash"])
        line = json.dumps(rec, default=str) + "\n"
        if path == "-":
            sys.stdout.write(line)
            sys.stdout.flush()
            return
        with open(path, "a") as f:
            f.write(line)


def verify(lines, key: str | None = None) -> tuple[bool, str]:
    """Check every chain in an audit log. (True, summary) or (False, where it breaks)."""
    k = key.encode() if key else None
    last: dict[str, tuple[int, str]] = {}
    n = 0
    for n, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            return False, f"line {n}: not JSON"
        if "hash" not in rec:
            return False, f"line {n}: not chained"
        got = rec.pop("hash")
        chain = rec.get("chain", "")
        seq, prev = last.get(chain, (0, GENESIS))
        if rec.get("seq") != seq + 1 or rec.get("prev") != prev:
            return False, f"line {n}: chain {chain} out of order or a record is missing"
        body = json.dumps(rec, sort_keys=True, separators=(",", ":"), default=str)
        if not hmac.compare_digest(_digest(prev, body, k), got):
            return False, f"line {n}: record was changed"
        last[chain] = (seq + 1, got)
    return True, f"{n} lines, {len(last)} chain(s) intact"


def sibling(path: str | None, suffix: str) -> str | None:
    """Where a secondary audit stream goes: next to the main file, or stdout too."""
    if not path:
        return None
    return path if path == "-" else path + suffix
