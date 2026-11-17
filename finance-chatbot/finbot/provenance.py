"""Value provenance: where each value an answer repeats came from.

Argument provenance (orchestrator._provenance) already keeps record text from choosing
which record is fetched next. This module applies the same idea to the answer, after
CaMeL (Debenedetti et al. 2025), which tags every value with its source and lets policy
decide where a value may flow.

A tool result mixes two kinds of value:

    trusted     fields our own systems set: status, amount, dates, codes, balances,
                policy guidance, help-page text and its source URL
    untrusted   text a third party wrote: a merchant's name and statement descriptor,
                a processor's free-text log message

Screening (finbot/untrusted.py) removes untrusted text that reads like an instruction.
Text that does not, such as a descriptor of "REFUND 480.00 USD BY 2026-12-01 CALL
1-888-555-0142", passes screening. Before this module the detail checks counted it as
evidence, so an answer repeating the amount, date or phone number counted as grounded.

Now an amount, date, time, percentage, code, card digit or currency in an answer must
appear in a TRUSTED field, and a phone number or email address may only be one our own
records or help pages carry. Untrusted text still reaches the model, which may quote a
merchant name; it just cannot vouch for a fact.
"""
from __future__ import annotations

import json
from typing import Any

from .dlp import EMAIL, PHONE

# Keys whose string values a third party writes. Matched at any depth, so a nested
# event ("last_authorization": {"merchant": ...}) is covered too.
UNTRUSTED_KEYS = frozenset({"merchant_name", "merchant", "descriptor", "message", "description", "memo",
                            "payee_name", "counterparty"})
REMOVED = "[third-party text]"


def trusted_view(value: Any) -> Any:
    """The value with every third-party string replaced. Numbers, booleans and our own
    strings are kept, so a merchant-supplied amount field still counts as an amount."""
    if isinstance(value, dict):
        return {k: (REMOVED if k in UNTRUSTED_KEYS and isinstance(v, str) else trusted_view(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [trusted_view(v) for v in value]
    return value


def trusted_blob(tool_log: list[dict]) -> str:
    """This turn's found tool data as JSON, third-party text removed: the evidence a stated
    detail may rest on."""
    return " ".join(json.dumps(trusted_view(c["result"]["data"])) for c in tool_log
                    if c["result"]["status"] == "found")


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())[-10:]


def contact_check(answer: str, tool_log: list[dict]) -> list[str]:
    """A phone number or email address in an answer must be one our own data carries.
    One taken from a descriptor or log message is a phishing lure, however plain it looks.
    The reason names the kind, not the value, so the audit log does not store it."""
    blob = trusted_blob(tool_log)
    phones = {_digits(p) for p in PHONE.findall(blob)}
    emails = {e.lower() for e in EMAIL.findall(blob)}
    reasons = []
    if any(_digits(p) not in phones for p in PHONE.findall(answer)):
        reasons.append("unverified_contact:phone")
    if any(e.lower() not in emails for e in EMAIL.findall(answer)):
        reasons.append("unverified_contact:email")
    return reasons
