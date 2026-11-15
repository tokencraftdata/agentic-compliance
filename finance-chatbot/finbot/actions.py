"""Write requests: detected in code, never executed by the bot.

finbot is read-only. When a user asks it to CHANGE something (refund, cancel,
retry, unblock, update details, change a limit, waive a fee, close an account),
code recognises the request before any model call, verifies the transaction
belongs to the caller, and opens a ticket for a person to carry out. The reply
is a fixed template, so the same request always gets the same handling.

Questions about how something works are not requests:
    "how do refunds work?"          -> answered normally
    "can I refund a partial amount?" -> answered normally
    "please refund txn_ABC123"       -> ticket
    "can you cancel txn_ABC123?"     -> ticket
    "I want a refund for txn_ABC123" -> ticket
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .tools import TXN_ID

# verb -> ticket kind
VERBS: dict[str, str] = {
    "refund": "refund", "reverse": "refund", "void": "refund", "credit back": "refund",
    "cancel": "cancel",
    "retry": "retry", "re-run": "retry", "rerun": "retry", "resubmit": "retry", "reprocess": "retry",
    "charge again": "retry",
    "unblock": "unblock", "unfreeze": "unblock", "release": "unblock",
    "update": "update_details", "change": "update_details",
    "increase": "limit_change", "raise": "limit_change",
    "waive": "fee_waiver",
    "close": "close_account",
}
_VERB = "|".join(sorted((re.escape(v) for v in VERBS), key=len, reverse=True))
_NOUN = {"a refund": "refund", "my money back": "refund", "a reversal": "refund", "a chargeback reversal": "refund",
         "a cancellation": "cancel", "a limit increase": "limit_change", "a fee waiver": "fee_waiver"}
_NOUNS = "|".join(re.escape(n) for n in _NOUN)

# An imperative ("refund txn_X", "cancel it") must name what to act on; "refund policy?" is a topic.
_TARGET = (r"(?:it|this|that|txn_\w+|the (?:payment|charge|transaction|order|fee|refund)s?|"
           r"my (?:payment|charge|transaction|order|card|account|limit|details|bank|email|address|payout)s?)\b")

REQUEST = re.compile(
    rf"(?:\b(?:can|could|would|will) you (?:please |just |kindly )?(?P<v1>{_VERB})\b"
    rf"|\bplease (?:just |kindly )?(?P<v2>{_VERB})\b"
    rf"|\bi(?: want| need| would like|'d like) (?:you to |to have you )?(?P<v3>{_VERB})\b"
    rf"|\bgo ahead and (?P<v4>{_VERB})\b"
    rf"|^\s*(?P<v5>{_VERB})\s+{_TARGET}"
    rf"|\bi(?: want| need| would like|'d like) (?P<n1>{_NOUNS})\b)", re.I)
QUESTION_ABOUT = re.compile(r"^\s*(how|what|why|when|where|which|is it|are|does|do i|can i|could i|should i|"
                            r"is there)\b", re.I)
# Changing these needs a person, but the target isn't a transaction.
ACCOUNT_KINDS = {"update_details", "limit_change", "close_account", "unblock", "fee_waiver"}


@dataclass(frozen=True)
class WriteRequest:
    kind: str
    txn_id: str | None
    verb: str


def detect(text: str, focus: str | None = None) -> WriteRequest | None:
    """A write request in the user's message, or None. Pure function of the text and focus."""
    if QUESTION_ABOUT.search(text) and not re.search(r"\b(can|could|would|will) you\b", text, re.I):
        return None
    m = REQUEST.search(text)
    if not m:
        return None
    verb = next(v for k, v in m.groupdict().items() if v)
    kind = _NOUN.get(verb.lower()) or VERBS.get(verb.lower(), "other")
    ids = TXN_ID.findall(text)
    txn = ids[-1] if ids else (focus if kind not in ACCOUNT_KINDS else None)
    return WriteRequest(kind, txn, verb.lower())


ACTION_WORDS = {"refund": "refund", "cancel": "cancel", "retry": "retry", "unblock": "unblock",
                "update_details": "update the details on", "limit_change": "change the limit on",
                "fee_waiver": "waive the fee on", "close_account": "close", "other": "act on"}
