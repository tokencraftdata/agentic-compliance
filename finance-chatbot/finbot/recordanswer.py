"""An answer built by code from the record, for when the model's answer can't be verified.

Without this, a draft that fails the check twice ends in "I won't guess", and two of those
hand the chat to a person, even though the record itself was read successfully. Here code
states what the record shows (status, amount, code, the policy next step) and nothing else.
Every field is validated before it is used, the wording comes from tables in code, and the
result still goes through the same verifier chain as a model answer.
"""
from __future__ import annotations

import re

from . import replies
from .processors.codes import CANONICAL, message_of

_ID = re.compile(r"^txn_[A-Za-z0-9]{6,}$")
_AMOUNT = re.compile(r"^\d+\.\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_KEY = re.compile(r"^[A-Z0-9_]{2,24}$")
_STATUS = {"approved", "declined", "failed", "refunded", "settled"}
_OPEN = {"unknown", "pending", "processing"}


def record_answer(tool_log: list[dict], focus: str | None) -> str | None:
    """The fixed-wording answer for the transaction this turn is about, or None when the
    turn has no single transaction lookup to state."""
    from .domains import by_tool, id_of, record_tools
    calls = [c for c in tool_log if c["name"] in record_tools() and c["result"]["status"] in ("found", "not_found")]
    if focus:
        calls = [c for c in calls if id_of(c["input"]) == focus]
    if not calls or len({id_of(c["input"]) for c in calls}) != 1:
        return None
    call = calls[-1]                       # the latest lookup (a repair may have re-fetched)
    domain = by_tool(call["name"])
    if domain is not None:                 # a domain plug-in's record: its own validated summary
        rid = str(id_of(call["input"]) or "")
        if not re.match(domain.pattern(), rid):
            return None
        if call["result"]["status"] == "not_found":
            return replies.RECORD_NOT_FOUND_ANY.format(id=rid)
        d = call["result"]["data"]
        line = domain.summary(d)
        if line is None:
            return None
        g = d.get("guidance") or {}        # computed by the domain's policy table, never record text
        if g.get("meaning") and g.get("next_step"):
            line += f" {g['meaning']} Next step: {g['next_step']}"
        if g.get("retry_rule"):
            line += f" Policy: {g['retry_rule']}"
        return f"{replies.RECORD_ONLY} {line}"
    tid = str(call["input"].get("txn_id", ""))
    if not _ID.match(tid):
        return None
    if call["result"]["status"] == "not_found":
        return replies.RECORD_NOT_FOUND.format(txn=tid)
    d = call["result"]["data"]
    status, amount, currency = str(d.get("status", "")).lower(), str(d.get("amount", "")), str(d.get("currency", ""))
    if not (_AMOUNT.match(amount) and _CURRENCY.match(currency)):
        return None
    if status in _OPEN:
        line = f"Transaction {tid} for {amount} {currency} has status {status}: its outcome is not confirmed yet."
    elif status in _STATUS:
        line = f"Transaction {tid} was {status} for {amount} {currency}"
        code, error = d.get("decline_code"), d.get("error")
        if isinstance(code, str) and code in CANONICAL:
            line += f" with decline code {code} ({message_of(code)})"
        elif isinstance(error, str) and _KEY.match(error):
            line += f" with gateway error {error}"
        line += "."
    else:
        return None
    g = d.get("guidance") or {}          # computed by finbot/policy.py, never record text
    if g.get("next_step") and g.get("retry_rule"):
        line += f" Next step: {g['next_step']} Retry policy: {g['retry_rule']}"
    return f"{replies.RECORD_ONLY} {line}"
