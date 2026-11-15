"""Adapters: one per processor dialect, each translating raw responses to canonical codes.

Three made-up processors, each with a dialect seen in real integrations:

  northstar   ISO 8583 style: {"resp": "51", "text": "INSUFF FUNDS"}
  meridian    refusal strings: {"result": "Refused", "refusalReason": "Not enough balance"}
  keystone    HTTP error objects: {"http": 402, "error": {"type": "card_error", "decline_code": "insufficient_funds"}}

Design choices:
  * An unknown raw response maps to UNMAPPED, never to the "closest" code. A
    guessed mapping is how a hard decline gets retried as a soft one.
  * The mapping tables are data. `encode` (canonical -> raw) exists so the
    simulator speaks each dialect, and so tests can check every code round-trips.
"""
from __future__ import annotations

from typing import Any, Protocol

from ..registry import Registry
from .codes import UNMAPPED, message_of, outcome_of
from .model import Normalized, Outcome


class ProcessorAdapter(Protocol):
    name: str

    def decode(self, raw: dict[str, Any]) -> Normalized: ...

    def encode(self, code: str) -> dict[str, Any]: ...


ADAPTERS: Registry = Registry("processor adapter")


def _normalized(code: str, message: str) -> Normalized:
    return Normalized(code, outcome_of(code), message)


def _unmapped(raw_code: str) -> Normalized:
    return Normalized(UNMAPPED, Outcome.UNMAPPED, f"unmapped response {raw_code!r}")


class NorthstarAdapter:
    """ISO 8583 response codes, mostly one-to-one with canonical codes."""

    name = "northstar"
    CODES = {"00": "00", "05": "05", "14": "14", "54": "54", "N7": "N7", "R1": "R1", "59": "59",
             "51": "51", "61": "61", "65": "65", "91": "91", "96": "PROCESSOR_ERROR"}
    _INVERSE = {v: k for k, v in CODES.items()}

    def decode(self, raw: dict[str, Any]) -> Normalized:
        if raw.get("timeout"):
            return _normalized("PROCESSOR_TIMEOUT", "no response")
        resp = str(raw.get("resp", ""))
        code = self.CODES.get(resp)
        return _normalized(code, str(raw.get("text", ""))) if code else _unmapped(resp)

    def encode(self, code: str) -> dict[str, Any]:
        if code == "PROCESSOR_TIMEOUT":
            return {"timeout": True}
        return {"resp": self._INVERSE[code], "text": message_of(code).upper()}


class MeridianAdapter:
    """Result plus a refusal reason string; the string carries the decision."""

    name = "meridian"
    REASONS = {"Do Not Honour": "05", "Invalid Card Number": "14", "Expired Card": "54", "CVC Declined": "N7",
               "Revocation Of Auth": "R1", "FRAUD": "59", "Not enough balance": "51",
               "Withdrawal amount exceeded": "61", "Withdrawal count exceeded": "65",
               "Issuer Unavailable": "91", "Acquirer Error": "PROCESSOR_ERROR"}
    _INVERSE = {v: k for k, v in REASONS.items()}

    def decode(self, raw: dict[str, Any]) -> Normalized:
        result = raw.get("result")
        if result == "Authorised":
            return _normalized("00", "Authorised")
        if result == "Pending":
            return _normalized("PROCESSOR_TIMEOUT", "pending without a final result")
        reason = str(raw.get("refusalReason", ""))
        code = self.REASONS.get(reason) if result in ("Refused", "Error") else None
        return _normalized(code, reason) if code else _unmapped(f"{result}/{reason}")

    def encode(self, code: str) -> dict[str, Any]:
        if code == "00":
            return {"result": "Authorised"}
        if code == "PROCESSOR_TIMEOUT":
            return {"result": "Pending"}
        result = "Error" if code == "PROCESSOR_ERROR" else "Refused"
        return {"result": result, "refusalReason": self._INVERSE[code]}


class KeystoneAdapter:
    """HTTP status plus an error object with a snake_case decline code."""

    name = "keystone"
    DECLINES = {"do_not_honor": "05", "incorrect_number": "14", "expired_card": "54", "incorrect_cvc": "N7",
                "revocation_of_authorization": "R1", "fraudulent": "59", "insufficient_funds": "51",
                "withdrawal_amount_limit_exceeded": "61", "withdrawal_count_limit_exceeded": "65",
                "issuer_not_available": "91"}
    _INVERSE = {v: k for k, v in DECLINES.items()}

    def decode(self, raw: dict[str, Any]) -> Normalized:
        http = int(raw.get("http", 0))
        if http == 200:
            return _normalized("00", "succeeded")
        if http == 504:
            return _normalized("PROCESSOR_TIMEOUT", "gateway timeout")
        if http >= 500:
            return _normalized("PROCESSOR_ERROR", f"HTTP {http}")
        decline = str((raw.get("error") or {}).get("decline_code", ""))
        code = self.DECLINES.get(decline) if http == 402 else None
        return _normalized(code, decline) if code else _unmapped(f"{http}/{decline}")

    def encode(self, code: str) -> dict[str, Any]:
        if code == "00":
            return {"http": 200}
        if code == "PROCESSOR_TIMEOUT":
            return {"http": 504}
        if code == "PROCESSOR_ERROR":
            return {"http": 503, "error": {"type": "api_error"}}
        return {"http": 402, "error": {"type": "card_error", "decline_code": self._INVERSE[code]}}


for _cls in (NorthstarAdapter, MeridianAdapter, KeystoneAdapter):
    ADAPTERS.register(_cls.name)(_cls)
