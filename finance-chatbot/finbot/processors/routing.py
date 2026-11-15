"""Routing: an ordered chain of rules turns "all processors" into a ranked candidate list.

Each rule is a small strategy object with one job: filter or reorder the
candidates. Rules record a RouteStep only when they change something, so the
stored trace explains the route ("keystone: no CAD"; "northstar skipped:
circuit open") without noise.

Order matters and is part of the design:
  1. capability  hard constraints first (currency, card brand)
  2. health      never send traffic to a processor whose breaker is open
  3. cost        cheapest first...
  4. local       ...then local acquiring wins over cost (stable sort keeps cost order within a tier)
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from ..registry import Registry
from .catalog import ProcessorDirectory
from .model import Payment, RoutePlan, RouteStep


@dataclass(frozen=True)
class RouteContext:
    directory: ProcessorDirectory
    available: Callable[[str], bool]   # health view: is this processor's circuit closed?


class RoutingRule(Protocol):
    name: str

    def apply(self, candidates: list[str], payment: Payment, ctx: RouteContext) -> tuple[list[str], str]:
        """Return the new candidate list and a detail string ("" when nothing changed)."""
        ...


RULES: Registry = Registry("routing rule")


@RULES.register("capability")
class CapabilityRule:
    name = "capability"

    def apply(self, candidates: list[str], payment: Payment, ctx: RouteContext) -> tuple[list[str], str]:
        kept, dropped = [], []
        for c in candidates:
            p = ctx.directory.profiles[c]
            if p.supports(payment.currency, payment.card_brand):
                kept.append(c)
            else:
                missing = payment.currency if payment.currency not in p.currencies else payment.card_brand
                dropped.append(f"{c}: no {missing}")
        return kept, "; ".join(dropped)


@RULES.register("health")
class HealthRule:
    name = "health"

    def apply(self, candidates: list[str], payment: Payment, ctx: RouteContext) -> tuple[list[str], str]:
        kept = [c for c in candidates if ctx.available(c)]
        skipped = [c for c in candidates if c not in kept]
        return kept, "; ".join(f"{c} skipped: circuit open" for c in skipped)


@RULES.register("cost")
class CostRule:
    name = "cost"

    def apply(self, candidates: list[str], payment: Payment, ctx: RouteContext) -> tuple[list[str], str]:
        ranked = sorted(candidates, key=lambda c: ctx.directory.profiles[c].fee_bps)
        return ranked, ("ranked by fee: " + ", ".join(ranked)) if ranked != candidates else ""


@RULES.register("local")
class LocalAcquiringRule:
    name = "local"

    def apply(self, candidates: list[str], payment: Payment, ctx: RouteContext) -> tuple[list[str], str]:
        def is_local(c: str) -> bool:
            return payment.merchant_country in ctx.directory.profiles[c].local_countries
        ranked = sorted(candidates, key=lambda c: not is_local(c))
        if ranked == candidates:
            return ranked, ""
        return ranked, f"local acquiring in {payment.merchant_country} first: {ranked[0]}"


DEFAULT_CHAIN = ("capability", "health", "cost", "local")


class Router:
    def __init__(self, directory: ProcessorDirectory, rules: Sequence[RoutingRule] | None = None) -> None:
        self.directory = directory
        self.rules: list[RoutingRule] = list(rules) if rules is not None else [RULES.create(n) for n in DEFAULT_CHAIN]

    def plan(self, payment: Payment, available: Callable[[str], bool]) -> RoutePlan:
        ctx = RouteContext(self.directory, available)
        candidates, steps = self.directory.names(), []
        for rule in self.rules:
            candidates, detail = rule.apply(candidates, payment, ctx)
            if detail:
                steps.append(RouteStep(rule.name, detail))
        return RoutePlan(tuple(candidates), tuple(steps))
