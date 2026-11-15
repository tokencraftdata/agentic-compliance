"""Cascade policy: may a failed attempt be retried on the NEXT processor?

Decided in code, like the retry policy, because the wrong answer costs money or
breaks card-network rules:

  * Issuer declines (hard, soft or fraud) are not cascaded. The same issuer
    decides on every processor, so the answer will not change, and every
    attempt counts toward the networks' reattempt limits.
  * A timeout is not cascaded. The first processor may have authorised the
    payment; sending it elsewhere risks charging the customer twice. Check the
    status first.
  * A processor error (the processor refused before sending to the network) or
    an issuer-unavailable response may cascade, up to `max_attempts` in total.
  * An unmapped response is not cascaded: we don't know what happened.

This is an example policy for the synthetic data. Replace it with your
networks' and acquirers' current rules before production.
"""
from __future__ import annotations

from collections.abc import Sequence

from .model import Attempt, CascadeDecision, Outcome

CASCADABLE = {Outcome.PROCESSOR_ERROR, Outcome.ISSUER_UNAVAILABLE}

REASONS = {
    Outcome.APPROVED: "Approved; nothing to cascade.",
    Outcome.HARD_DECLINE: "Issuer decline: the same issuer decides on every processor, so cascading will not "
                          "change the answer and counts toward card-network reattempt limits.",
    Outcome.SOFT_DECLINE: "Issuer soft decline: the same issuer decides on every processor; wait and retry under "
                          "the retry policy instead of cascading.",
    Outcome.FRAUD_DECLINE: "Issuer suspects fraud: never cascade; the cardholder must verify with their bank.",
    Outcome.TIMEOUT: "Outcome unknown: the first processor may have authorised it. Check the status before any "
                     "retry; cascading risks a double charge.",
    Outcome.UNMAPPED: "Unmapped processor response: do not cascade until the response is understood.",
    Outcome.PROCESSOR_ERROR: "Processor error before the request reached the card network, so it is safe to "
                             "try the next processor.",
    Outcome.ISSUER_UNAVAILABLE: "Issuer or network unavailable; another processor may reach the issuer.",
}


class CascadePolicy:
    def __init__(self, max_attempts: int = 3) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.max_attempts = max_attempts

    def decide(self, last: Attempt, attempts: Sequence[Attempt], remaining: Sequence[str]) -> CascadeDecision:
        if last.outcome not in CASCADABLE:
            return CascadeDecision(False, REASONS[last.outcome])
        if len(attempts) >= self.max_attempts:
            return CascadeDecision(False, f"Attempt limit reached ({self.max_attempts} per payment).")
        if not remaining:
            return CascadeDecision(False, "No other eligible processor.")
        return CascadeDecision(True, REASONS[last.outcome])

    @staticmethod
    def advice(outcome: Outcome) -> CascadeDecision:
        """Could a payment that ended with this outcome be sent to another processor?"""
        return CascadeDecision(outcome in CASCADABLE, REASONS[outcome])
