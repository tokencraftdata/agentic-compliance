"""Value types shared by the processor-orchestration layer.

Everything here is a frozen dataclass or a plain enum: no I/O, no clocks, no
randomness. The router, the cascade policy, the simulator and the bot's tools
all exchange these types, so each piece can be tested on its own.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    """What one processor attempt meant, independent of the processor's dialect."""

    APPROVED = "approved"
    SOFT_DECLINE = "soft_decline"          # issuer said no for now (funds, limits)
    HARD_DECLINE = "hard_decline"          # issuer said no; same card details will keep failing
    FRAUD_DECLINE = "fraud_decline"        # issuer suspects fraud
    ISSUER_UNAVAILABLE = "issuer_unavailable"  # issuer or network could not answer
    PROCESSOR_ERROR = "processor_error"    # processor refused or failed BEFORE sending to the network
    TIMEOUT = "timeout"                    # no answer: the payment may or may not exist
    UNMAPPED = "unmapped"                  # a response we have no mapping for; never guess


@dataclass(frozen=True)
class Payment:
    """The fields routing decisions are allowed to depend on."""

    txn_id: str
    amount: str
    currency: str
    card_brand: str
    merchant_country: str
    channel: str = "ecommerce"
    issuer_country: str = ""


@dataclass(frozen=True)
class Normalized:
    """A processor response translated into finbot's canonical codes."""

    code: str            # canonical code, ISO-8583 style where one exists ("05", "51", "PROCESSOR_ERROR")
    outcome: Outcome
    message: str = ""    # human text from the processor; third-party data, screened before the model sees it


@dataclass(frozen=True)
class Attempt:
    n: int
    processor: str
    raw_code: str
    code: str
    outcome: Outcome
    latency_ms: int
    cascaded: bool = False          # was another processor tried after this one?
    cascade_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


@dataclass(frozen=True)
class RouteStep:
    """One routing rule's effect, kept so the bot can explain why a processor was chosen."""

    rule: str
    detail: str


@dataclass(frozen=True)
class RoutePlan:
    candidates: tuple[str, ...]
    steps: tuple[RouteStep, ...] = ()


@dataclass(frozen=True)
class CascadeDecision:
    allowed: bool
    reason: str


@dataclass
class RoutingTrace:
    """The full record of how one payment was routed. Stored on the transaction."""

    routed: bool
    plan: RoutePlan = field(default_factory=lambda: RoutePlan(()))
    attempts: list[Attempt] = field(default_factory=list)
    not_routed_reason: str = ""

    @property
    def final(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    def to_dict(self) -> dict[str, Any]:
        if not self.routed:
            return {"routed": False, "not_routed_reason": self.not_routed_reason}
        return {"routed": True, "candidates": list(self.plan.candidates),
                "route_steps": [asdict(s) for s in self.plan.steps],
                "attempts": [a.to_dict() for a in self.attempts]}
