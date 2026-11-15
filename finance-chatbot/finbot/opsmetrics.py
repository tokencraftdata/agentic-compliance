"""What strictness costs, read from the turn audit log.

The gate trades resolved chats for correctness: a failed check means a second model call,
a record-only answer, a refusal, or a handoff. This puts numbers on that trade for any
run, offline or live, so thresholds are set from data instead of by feel.
"""
from __future__ import annotations

from typing import Any

from .adaptive.signals import _records

REFUSALS = ("evidence_guard", "output_guardrail")


def _pct(sorted_values: list[int], q: float) -> int | None:
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))] if sorted_values else None


def summarise(audit_path: str) -> dict[str, Any]:
    turns = [r for r in _records(audit_path) if "blocked_by" in r]
    n = len(turns)
    if not n:
        return {"turns": 0}
    model_turns = [r for r in turns if r.get("models")]
    calls = sum(len(r["models"]) for r in model_turns)
    latency = sorted(int(r.get("latency_ms", 0)) for r in turns)
    blocked: dict[str, int] = {}
    for r in turns:
        if r.get("blocked_by"):
            blocked[r["blocked_by"]] = blocked.get(r["blocked_by"], 0) + 1

    def rate(k: int) -> float:
        return round(k / n, 4)

    records_only = sum(any(str(x).startswith("records_only:") for x in r.get("reasons", [])) for r in turns)
    return {
        "turns": n,
        "answered_rate": rate(sum(r.get("blocked_by") is None for r in turns)),
        "repair_rate": rate(sum(bool(r.get("repaired")) for r in turns)),
        "record_only_rate": rate(records_only),
        "refusal_rate": rate(sum(r.get("blocked_by") in REFUSALS for r in turns)),
        "handoff_rate": rate(sum(bool(r.get("escalated")) for r in turns)),
        "blocked_by": dict(sorted(blocked.items())),
        "model_calls_per_model_turn": round(calls / len(model_turns), 2) if model_turns else None,
        "latency_ms": {"p50": _pct(latency, 0.5), "p95": _pct(latency, 0.95)},
    }
