"""Plain-word reasons, checked against the record.

The evidence check compares numbers, codes and statuses. It does not notice an answer
that quotes the right code and then explains it wrongly: "declined with code 05 because
the card had expired". This module closes part of that gap. Each known failure key maps
to one cause, each cause to the phrases people use for it, and an answer that affirms a
cause the records of this turn don't carry is blocked.

It is still a phrase table. A cause worded in a way the table doesn't know gets through,
which is why free-text answers also go through the output guardrail's grounding check.
"""
from __future__ import annotations

import re
from typing import Any

from .guardrails import _affirmed, _sentences

# failure key (decline code or gateway error) -> cause
KEY_CAUSE: dict[str, str] = {
    "05": "no_reason_given", "51": "insufficient_funds", "54": "expired_card", "14": "invalid_card",
    "61": "limit", "65": "limit", "N7": "security_code", "59": "fraud", "91": "issuer_unavailable",
    "R1": "revoked", "RISK_BLOCKED": "risk_block", "3DS_FAILED": "three_ds", "AVS_MISMATCH": "address",
    "PROCESSOR_TIMEOUT": "timeout", "PROCESSOR_ERROR": "processor_error", "NO_ROUTE": "no_route",
    "DUPLICATE_REQUEST": "duplicate",
}

_SEC = r"(cvv2?|cvc|security code)"
CAUSE_PHRASES: dict[str, re.Pattern[str]] = {k: re.compile(v, re.I) for k, v in {
    "insufficient_funds": r"\b(insufficient funds|not enough (money|funds|balance)|lack of funds|low balance|"
                          r"ran out of (money|funds))\b",
    "expired_card": r"\b(card (has |had |is |was )?expired|expired card|expiry date (has |had )?passed)\b",
    "invalid_card": r"\b(invalid card number|card number (is|was) (invalid|wrong|incorrect|mistyped)|"
                    r"(wrong|incorrect) card number)\b",
    "security_code": rf"\b({_SEC} (mismatch|did ?n[o']t match|(is|was) (wrong|incorrect))|"
                     rf"(wrong|incorrect) {_SEC})\b",
    "fraud": r"\b(suspected fraud|suspects? fraud|fraud (suspicion|check|block)|"
             r"flagged (it |this |the payment )?as (fraud|fraudulent|suspicious))\b",
    "limit": r"\b(exceed(s|ed)?|over|above|reached|hit) (the |its |their |a )?"
             r"((card|withdrawal|spending|daily|amount|frequency) )*limit\b",
    "issuer_unavailable": r"\b(issuer|issuing bank|bank) (was|is) (temporarily )?(unavailable|unreachable|offline)\b",
    "revoked": r"\b(revoked (the |their )?authori[sz]ation|revocation of authori[sz]ation|"
               r"cancell?ed the (subscription|recurring))\b",
    "risk_block": r"\b(blocked by (the merchant's |your |a |the )?(own )?risk|risk (rule|rules|settings) blocked)\b",
    "three_ds": r"\b(3-?d ?secure|3ds)\b",
    "address": r"\b(billing )?address (mismatch|did ?n[o']t match)\b",
    "timeout": r"\b(timed out|time-?out|did not respond in time)\b",
    "processor_error": r"\bprocessor (error|fault|failure)\b",
    "duplicate": r"\b(duplicate (request|payment)|idempotency key (was )?reused)\b",
}.items()}

_KEY_FIELDS = {"decline_code", "error", "code", "final_code", "reason_key"}
_TXN_TOOLS = {"get_transaction", "search_transaction_logs", "get_routing_trace"}


def _keys(v: Any) -> set[str]:
    """Every failure key anywhere in a tool result (record, log events, routing attempts)."""
    if isinstance(v, dict):
        own = {str(x) for k, x in v.items() if k in _KEY_FIELDS and isinstance(x, str)}
        return own.union(*(_keys(x) for x in v.values()))
    if isinstance(v, list):
        return set().union(*(_keys(x) for x in v)) if v else set()
    return set()


def reason_check(answer: str, tool_log: list[dict]) -> list[str]:
    """Reasons that contradict the record. Applies only when exactly one failed transaction
    was looked up this turn, so a help page listing many codes is not judged by it."""
    found = [c for c in tool_log if c["result"]["status"] == "found" and c["name"] in _TXN_TOOLS]
    records = [c["result"]["data"] for c in found if c["name"] == "get_transaction"]
    if len(records) != 1 or str(records[0].get("status", "")).lower() in ("approved", "succeeded", "settled"):
        return []
    keys = set().union(*(_keys(c["result"]["data"]) for c in found))
    supported = {KEY_CAUSE[k] for k in keys if k in KEY_CAUSE}
    if not supported:
        return []           # an unmapped failure: nothing to compare against
    record_key = records[0].get("decline_code") or records[0].get("error") or "unknown"
    reasons = []
    for cause, pattern in CAUSE_PHRASES.items():
        if cause not in supported and any(_affirmed(pattern, s) for s in _sentences(answer)):
            reasons.append(f"reason_mismatch:record={record_key},claimed={cause}")
    return reasons
