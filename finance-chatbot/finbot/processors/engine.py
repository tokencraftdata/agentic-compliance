"""PaymentOrchestrator: route, attempt, normalise, decide whether to cascade, record.

Ports and collaborators (all injected, so tests and the simulator swap them):
  clients    name -> ProcessorClient (the processor's API; simulated offline)
  adapters   name -> ProcessorAdapter (dialect -> canonical code)
  router     Router (rule chain)
  cascade    CascadePolicy
  breakers   one CircuitBreaker per processor, on the orchestrator's clock

Only processor-side failures (errors, timeouts) count against a processor's
breaker. An issuer decline means the processor worked.

finbot itself is read-only: the support bot reads the RoutingTrace this
produces, it never calls `authorize`. The orchestrator is here so the synthetic
data comes from the same rules the bot explains, and so the rules can be tested.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from ..resilience import CircuitBreaker
from .adapters import ProcessorAdapter
from .cascade import CascadePolicy
from .codes import PROCESSOR_FAULTS
from .model import Attempt, Outcome, Payment, RoutingTrace
from .routing import Router


class ProcessorUnavailable(Exception):
    """Connection refused or rejected before the request was sent to the card network."""


class ProcessorClient(Protocol):
    def authorize(self, payment: Payment, idempotency_key: str, at: str) -> tuple[dict[str, Any], int]:
        """Return (raw response in the processor's dialect, latency in ms)."""
        ...


def epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


class PaymentOrchestrator:
    def __init__(self, clients: Mapping[str, ProcessorClient], adapters: Mapping[str, ProcessorAdapter],
                 router: Router, cascade: CascadePolicy, breaker_threshold: int = 3,
                 breaker_cooldown_s: float = 600) -> None:
        missing = set(router.directory.names()) - set(clients) | set(router.directory.names()) - set(adapters)
        if missing:
            raise ValueError(f"no client or adapter for: {sorted(missing)}")
        self.clients, self.adapters, self.router, self.cascade = clients, adapters, router, cascade
        self._now = 0.0
        self.breakers = {n: CircuitBreaker(n, threshold=breaker_threshold, cooldown_s=breaker_cooldown_s,
                                           max_cooldown_s=breaker_cooldown_s * 4, clock=lambda: self._now)
                         for n in router.directory.names()}

    def authorize(self, payment: Payment, at: str) -> RoutingTrace:
        self._now = epoch(at)
        plan = self.router.plan(payment, lambda n: self.breakers[n].available())
        trace = RoutingTrace(routed=True, plan=plan)
        if not plan.candidates:
            trace.routed, trace.not_routed_reason = False, "No processor supports this payment or all are unavailable."
            return trace
        queue = list(plan.candidates)
        while queue:
            name = queue.pop(0)
            attempt = self._attempt(name, payment, at, len(trace.attempts) + 1)
            decision = self.cascade.decide(attempt, [*trace.attempts, attempt], queue)
            if decision.allowed:
                attempt = replace(attempt, cascaded=True, cascade_reason=decision.reason)
            trace.attempts.append(attempt)
            if not decision.allowed:
                break
        return trace

    def _attempt(self, name: str, payment: Payment, at: str, n: int) -> Attempt:
        breaker = self.breakers[name]
        if not breaker.allow():   # open, or half-open with its one probe already in flight: don't call it
            return Attempt(n, name, "circuit open", "PROCESSOR_ERROR", Outcome.PROCESSOR_ERROR, 0)
        try:
            raw, latency = self.clients[name].authorize(payment, f"{payment.txn_id}:{name}", at)
            norm = self.adapters[name].decode(raw)
            raw_code = _raw_code(raw)
        except ProcessorUnavailable as e:
            norm_code, outcome, latency, raw_code = "PROCESSOR_ERROR", Outcome.PROCESSOR_ERROR, 0, f"unavailable: {e}"
            breaker.failure()
            return Attempt(n, name, raw_code, norm_code, outcome, latency)
        if norm.outcome in PROCESSOR_FAULTS:
            breaker.failure()
        else:
            breaker.success()
        return Attempt(n, name, raw_code, norm.code, norm.outcome, latency)


def _raw_code(raw: Mapping[str, Any]) -> str:
    """A short, stable rendering of the raw response for the trace."""
    for key in ("resp", "refusalReason", "result"):
        if key in raw:
            return str(raw[key])
    if "http" in raw:
        decline = (raw.get("error") or {}).get("decline_code")
        return f"{raw['http']}" + (f"/{decline}" if decline else "")
    return "timeout" if raw.get("timeout") else "unknown"
