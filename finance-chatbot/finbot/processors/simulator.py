"""Offline simulator: processors that speak their dialect, fail, slow down and go dark.

  SimulatedProcessor   a ProcessorClient. Outage windows raise ProcessorUnavailable;
                       a random error rate returns the dialect's processor error;
                       otherwise it returns the issuer's decision in its own dialect.
  IssuerModel          decides the card issuer's answer for a payment. The issuer
                       is the same whichever processor carries the payment, which
                       is why cascading an issuer decline never helps.
  Scenario             a named set of processor faults, for scripts/simulate_routing.py.

Everything is seeded: the same seed gives the same traces.
"""
from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .adapters import ADAPTERS, ProcessorAdapter
from .cascade import CascadePolicy
from .catalog import Incident, ProcessorDirectory
from .engine import PaymentOrchestrator, ProcessorUnavailable
from .model import Payment
from .routing import Router

IssuerModel = Callable[[Payment], str]   # payment -> canonical code ("00", "51", "PROCESSOR_TIMEOUT", ...)


@dataclass
class SimulatedProcessor:
    adapter: ProcessorAdapter
    issuer: IssuerModel
    rng: random.Random
    incidents: Sequence[Incident] = ()
    error_rate: float = 0.0
    latency_ms: tuple[int, int] = (120, 600)

    def authorize(self, payment: Payment, idempotency_key: str, at: str) -> tuple[dict[str, Any], int]:
        if any(i.covers(at) and i.kind == "outage" for i in self.incidents):
            raise ProcessorUnavailable("connection refused")
        degraded = any(i.covers(at) and i.kind == "degraded" for i in self.incidents)
        latency = self.rng.randint(*self.latency_ms) * (4 if degraded else 1)
        if self.rng.random() < self.error_rate * (5 if degraded else 1):
            return self.adapter.encode("PROCESSOR_ERROR"), latency
        code = self.issuer(payment)
        return self.adapter.encode(code), (30000 if code == "PROCESSOR_TIMEOUT" else latency)


def weighted_issuer(rng: random.Random, weights: Mapping[str, float]) -> IssuerModel:
    codes, w = list(weights), list(weights.values())
    last: list[str] = ["", ""]   # (txn_id, code): a payment's attempts are consecutive, so one entry is enough

    def decide(p: Payment) -> str:
        # One decision per payment: a cascade reaches the same issuer and gets the same answer.
        if last[0] != p.txn_id:
            last[:] = [p.txn_id, rng.choices(codes, weights=w)[0]]
        return last[1]
    return decide


def build_orchestrator(directory: ProcessorDirectory, issuer: IssuerModel, rng: random.Random,
                       error_rates: Mapping[str, float] | None = None, max_attempts: int = 3) -> PaymentOrchestrator:
    """Wire simulated clients, the registered adapters, the default router and cascade policy."""
    adapters = {n: ADAPTERS.create(n) for n in directory.names()}
    clients = {n: SimulatedProcessor(adapters[n], issuer, rng, directory.incidents_for(n),
                                     (error_rates or {}).get(n, 0.0)) for n in directory.names()}
    return PaymentOrchestrator(clients, adapters, Router(directory), CascadePolicy(max_attempts))


# ------------------------------------------------------------------ scenarios

STEADY_ISSUER = {"00": 80, "05": 5, "51": 5, "54": 1, "14": 1, "N7": 1, "59": 1, "61": 1, "65": 1, "91": 2,
                 "R1": 0.5, "PROCESSOR_TIMEOUT": 0.5}


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    incidents: tuple[Incident, ...] = ()
    error_rates: Mapping[str, float] = field(default_factory=dict)
    issuer: Mapping[str, float] = field(default_factory=lambda: dict(STEADY_ISSUER))


DAY = "2026-11-20"
SCENARIOS = {s.name: s for s in (
    Scenario("steady", "Normal day: small processor error rates, typical issuer mix.",
             error_rates={"northstar": 0.01, "meridian": 0.01, "keystone": 0.01}),
    Scenario("northstar_outage", "northstar refuses connections from 10:00 to 12:00.",
             incidents=(Incident("northstar", f"{DAY}T10:00:00Z", f"{DAY}T12:00:00Z", "outage",
                                 "northstar refused connections"),),
             error_rates={"northstar": 0.01, "meridian": 0.01, "keystone": 0.01}),
    Scenario("keystone_degraded", "keystone is slow and error-prone all day.",
             incidents=(Incident("keystone", f"{DAY}T00:00:00Z", f"{DAY}T23:59:59Z", "degraded",
                                 "keystone elevated errors and latency"),),
             error_rates={"northstar": 0.01, "meridian": 0.01, "keystone": 0.05}),
    Scenario("issuer_outage", "A large issuer is down: many 91 responses. Cascading cannot fix this.",
             issuer={**STEADY_ISSUER, "91": 25}),
)}


@dataclass
class SimulationReport:
    scenario: str
    payments: int = 0
    approved: int = 0
    cascaded: int = 0
    attempts: int = 0
    unknown_outcome: int = 0         # timeouts, left uncascaded on purpose
    not_routed: int = 0
    by_processor: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def approval_rate(self) -> float:
        return self.approved / self.payments if self.payments else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"scenario": self.scenario, "payments": self.payments,
                "approval_rate": round(self.approval_rate, 4), "cascaded": self.cascaded,
                "attempts_per_payment": round(self.attempts / max(self.payments, 1), 3),
                "unknown_outcome": self.unknown_outcome, "not_routed": self.not_routed,
                "by_processor": self.by_processor}


def run_scenario(scenario: Scenario, payments: int = 2000, seed: int = 7,
                 directory: ProcessorDirectory | None = None) -> SimulationReport:
    """Push `payments` synthetic payments through one simulated day, in time order."""
    rng = random.Random(seed)
    base = directory or ProcessorDirectory.default()
    day = ProcessorDirectory(base.profiles, [*base.incidents, *scenario.incidents])
    orch = build_orchestrator(day, weighted_issuer(rng, scenario.issuer), rng, scenario.error_rates)
    report = SimulationReport(scenario.name)
    brands = ["visa", "visa", "mastercard", "mastercard", "amex", "discover"]
    currencies = ["USD"] * 6 + ["EUR", "GBP", "CAD"]
    for i in range(payments):
        seconds = int(i * 86400 / payments)
        at = f"{DAY}T{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}Z"
        p = Payment(f"sim_{i:06d}", f"{rng.uniform(5, 400):.2f}", rng.choice(currencies), rng.choice(brands),
                    rng.choice(["US", "US", "US", "CA", "GB"]))
        trace = orch.authorize(p, at)
        report.payments += 1
        if not trace.routed or trace.final is None:
            report.not_routed += 1
            continue
        report.attempts += len(trace.attempts)
        report.cascaded += len(trace.attempts) > 1
        report.approved += trace.final.code == "00"
        report.unknown_outcome += trace.final.code == "PROCESSOR_TIMEOUT"
        for a in trace.attempts:
            row = report.by_processor.setdefault(a.processor, {"attempts": 0, "approved": 0, "processor_errors": 0})
            row["attempts"] += 1
            row["approved"] += a.code == "00"
            row["processor_errors"] += a.code == "PROCESSOR_ERROR"
    return report
