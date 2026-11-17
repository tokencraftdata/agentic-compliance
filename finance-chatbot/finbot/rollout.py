"""Canary rollout decisions: promote, wait, or roll back, from the canary's own behaviour.

A new version takes a growing share of traffic (for example 10%, 50%, 100%). After each step it
bakes for a while, then its metrics are compared with the baseline (the version it replaces).
The comparison is about what finbot exists to do, not only about errors:

    egress_leak          card data found in an outgoing answer         any → roll back now
    unsafe_output_rate   planted links, secret requests, contact lures  > max(2x baseline, 0.5%) → roll back
    unavailable_rate     turns that ended "try again"                   > baseline + 2 points → roll back
    refusal_rate         turns answered "I won't guess" / blocked       > baseline + 5 points → roll back
    p95_ms               turn latency                                    > 1.5x baseline → roll back
    turns                traffic seen at this step                       < min_turns → wait (not enough data)

Metrics come from the turn audit log and security events (or a metrics backend built on them);
this module only decides. scripts/rollout.py applies the decision with kubectl.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Metrics:
    turns: int
    refusal_rate: float = 0.0
    unavailable_rate: float = 0.0
    unsafe_output_rate: float = 0.0
    egress_leaks: int = 0
    p95_ms: float = 0.0


@dataclass
class Limits:
    min_turns: int = 200
    refusal_points: float = 0.05
    unavailable_points: float = 0.02
    unsafe_floor: float = 0.005
    latency_factor: float = 1.5


@dataclass
class Decision:
    action: str                    # promote | wait | rollback
    reasons: list[str] = field(default_factory=list)


def decide(canary: Metrics, baseline: Metrics, limits: Limits | None = None) -> Decision:
    lim = limits or Limits()
    if canary.egress_leaks > 0:                       # never wait for more data on a card leak
        return Decision("rollback", [f"egress_leak:{canary.egress_leaks}"])
    if canary.turns < lim.min_turns:
        return Decision("wait", [f"turns:{canary.turns}<{lim.min_turns}"])
    reasons = []
    if canary.unsafe_output_rate > max(2 * baseline.unsafe_output_rate, lim.unsafe_floor):
        reasons.append(f"unsafe_output_rate:{canary.unsafe_output_rate:.3f}")
    if canary.unavailable_rate > baseline.unavailable_rate + lim.unavailable_points:
        reasons.append(f"unavailable_rate:{canary.unavailable_rate:.3f}")
    if canary.refusal_rate > baseline.refusal_rate + lim.refusal_points:
        reasons.append(f"refusal_rate:{canary.refusal_rate:.3f}")
    if baseline.p95_ms and canary.p95_ms > lim.latency_factor * baseline.p95_ms:
        reasons.append(f"p95_ms:{canary.p95_ms:.0f}")
    return Decision("rollback", reasons) if reasons else Decision("promote")


def metrics_from_audit(lines, version_of=lambda rec: rec.get("version")) -> dict[str, Metrics]:
    """Per-version metrics from turn audit lines (stream "turn") and security events. Each turn
    record must carry the serving version; `version_of` reads it."""
    import json
    turns: dict[str, list[dict]] = {}
    unsafe: dict[str, int] = {}
    leaks: dict[str, int] = {}
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        v = version_of(rec)
        if v is None:
            continue
        if rec.get("stream") == "turn":
            turns.setdefault(v, []).append(rec)
        elif rec.get("stream") == "security" and rec.get("class") == "unsafe_output":
            unsafe[v] = unsafe.get(v, 0) + 1
        elif rec.get("stream") == "security" and rec.get("class") == "egress_leak":
            leaks[v] = leaks.get(v, 0) + 1
    out = {}
    for v, recs in turns.items():
        n = len(recs)
        lat = sorted(r.get("latency_ms", 0) for r in recs)
        out[v] = Metrics(
            turns=n,
            refusal_rate=sum(1 for r in recs if r.get("blocked_by") in ("evidence_guard", "output_guardrail")) / n,
            unavailable_rate=sum(1 for r in recs if r.get("blocked_by") == "unavailable") / n,
            unsafe_output_rate=unsafe.get(v, 0) / n,
            egress_leaks=leaks.get(v, 0),
            p95_ms=float(lat[min(n - 1, int(0.95 * n))]) if lat else 0.0)
    return out
