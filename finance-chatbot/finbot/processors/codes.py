"""Canonical response codes: one vocabulary for every processor.

Processors report the same issuer decision in different dialects (a numeric
ISO 8583 code, a refusal string, an HTTP error object). Adapters translate each
dialect into these canonical codes. Retry policy (finbot/policy.py), cascade
policy and the evidence checks only ever see canonical codes.

The codes match the decline codes used elsewhere in finbot, so a record's
`decline_code` and the last routing attempt's `code` are the same value.
"""
from __future__ import annotations

from .model import Outcome

CANONICAL: dict[str, tuple[Outcome, str]] = {
    "00": (Outcome.APPROVED, "Approved"),
    "05": (Outcome.HARD_DECLINE, "Do not honor"),
    "14": (Outcome.HARD_DECLINE, "Invalid card number"),
    "54": (Outcome.HARD_DECLINE, "Expired card"),
    "N7": (Outcome.HARD_DECLINE, "CVV2 mismatch"),
    "R1": (Outcome.HARD_DECLINE, "Revocation of authorization"),
    "59": (Outcome.FRAUD_DECLINE, "Suspected fraud"),
    "51": (Outcome.SOFT_DECLINE, "Insufficient funds"),
    "61": (Outcome.SOFT_DECLINE, "Exceeds withdrawal amount limit"),
    "65": (Outcome.SOFT_DECLINE, "Exceeds withdrawal frequency limit"),
    "91": (Outcome.ISSUER_UNAVAILABLE, "Issuer unavailable"),
    "PROCESSOR_ERROR": (Outcome.PROCESSOR_ERROR, "Processor error before the request reached the card network"),
    "PROCESSOR_TIMEOUT": (Outcome.TIMEOUT, "Processor did not respond in time"),
}

UNMAPPED = "UNMAPPED"

# Failures on the processor's side (not the issuer's): they count against a processor's health.
PROCESSOR_FAULTS = frozenset({Outcome.PROCESSOR_ERROR, Outcome.TIMEOUT})
PROCESSOR_FAULT_CODES = frozenset(c for c, (o, _) in CANONICAL.items() if o in PROCESSOR_FAULTS)


def outcome_of(code: str) -> Outcome:
    entry = CANONICAL.get(code)
    return entry[0] if entry else Outcome.UNMAPPED


def message_of(code: str) -> str:
    entry = CANONICAL.get(code)
    return entry[1] if entry else "Unmapped processor response"
