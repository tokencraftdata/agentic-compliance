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
from collections.abc import Callable
from dataclasses import dataclass, field

from .domains import by_id, find_ids
from .domains.base import _PREFIXES  # filled by importing finbot.domains, above
from .resilience import ServiceUnavailable
from .tickets import Ticket, TicketStore, open_ticket
from .tools import Session

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
    "approve": "decision_request", "verify": "decision_request",
    "extend": "payment_arrangement", "defer": "payment_arrangement", "postpone": "payment_arrangement",
    "recall": "cancel", "stop": "cancel",
    "pause": "subscription_change", "downgrade": "subscription_change", "upgrade": "subscription_change",
    "appeal": "decision_request", "dispute": "dispute_request", "accept": "dispute_decision",
}
_VERB = "|".join(sorted((re.escape(v) for v in VERBS), key=len, reverse=True))
_NOUN = {"a refund": "refund", "my money back": "refund", "a reversal": "refund", "a chargeback reversal": "refund",
         "a cancellation": "cancel", "a limit increase": "limit_change", "a fee waiver": "fee_waiver"}
_NOUNS = "|".join(re.escape(n) for n in _NOUN)

# An imperative ("refund txn_X", "cancel it") must name what to act on; "refund policy?" is a topic.
_TARGET = (rf"(?:it|this|that|(?:{'|'.join(_PREFIXES)})_\w+|"
           r"the (?:payment|charge|transaction|order|fee|refund|transfer|hold|loan|application|account)s?|"
           r"my (?:payment|charge|transaction|order|card|account|limit|details|bank|email|address|payout|"
           r"transfer|loan|application|identity|due date|subscription|claim|dispute|mortgage|plan)s?)\b")

REQUEST = re.compile(
    rf"(?:\b(?:can|could|would|will) you (?:please |just |kindly )?(?P<v1>{_VERB})\b"
    rf"|\bplease (?:just |kindly )?(?P<v2>{_VERB})\b"
    rf"|\bi(?: want| need| would like|'d like) (?:you to |to have you )?(?P<v3>{_VERB})\b"
    rf"|\bi(?: want| would like|'d like) to (?P<v6>{_VERB})\b"
    rf"|\bgo ahead and (?P<v4>{_VERB})\b"
    rf"|^\s*(?P<v5>{_VERB})\s+{_TARGET}"
    rf"|\bi(?: want| need| would like|'d like) (?P<n1>{_NOUNS})\b)", re.I)
QUESTION_ABOUT = re.compile(r"^\s*(how|what|why|when|where|which|is it|are|does|do i|can i|could i|should i|"
                            r"is there)\b", re.I)
# Changing these needs a person, but the target isn't a transaction.
ACCOUNT_KINDS = {"update_details", "limit_change", "close_account", "unblock", "fee_waiver",
                 "decision_request", "payment_arrangement"}


@dataclass(frozen=True)
class WriteRequest:
    kind: str
    txn_id: str | None
    verb: str
    text: str = ""          # the (masked) message the request came from


def detect(text: str, focus: str | None = None) -> WriteRequest | None:
    """A write request in the user's message, or None. Pure function of the text and focus."""
    if QUESTION_ABOUT.search(text) and not re.search(r"\b(can|could|would|will) you\b", text, re.I):
        return None
    m = REQUEST.search(text)
    if not m:
        return None
    verb = next(v for k, v in m.groupdict().items() if v)
    kind = _NOUN.get(verb.lower()) or VERBS.get(verb.lower(), "other")
    ids = find_ids(text)
    txn = ids[-1] if ids else (focus if kind not in ACCOUNT_KINDS else None)
    return WriteRequest(kind, txn, verb.lower(), text)


ACTION_WORDS = {"refund": "refund", "cancel": "cancel", "retry": "retry", "unblock": "unblock",
                "update_details": "update the details on", "limit_change": "change the limit on",
                "fee_waiver": "waive the fee on", "close_account": "close", "other": "act on",
                "decision_request": "review", "payment_arrangement": "review the payment terms on",
                "subscription_change": "change", "dispute_request": "open a dispute on",
                "dispute_decision": "record your decision on"}


# ------------------------------------------------------------------ intake: request -> ticket

@dataclass(frozen=True)
class TicketOutcome:
    ticket: Ticket | None              # None when the transaction isn't the caller's
    created: bool = False              # False: the same request was already open
    policy_note: str = ""
    tool_log: list[dict] = field(default_factory=list)


class TicketIntake:
    """Turns a write request into a ticket. Knows tickets and retry policy; knows nothing
    about chat, history or replies (the orchestrator renders those).

    `lookup(args)` is the session's get_transaction tool (already tenant-scoped and
    screened), so a request about someone else's transaction finds nothing."""

    FACT_KEYS = ("status", "amount", "currency", "decline_code", "error")

    def __init__(self, store: TicketStore):
        self.store = store

    def handle(self, req: WriteRequest, session: Session, lookup: Callable[[dict], dict]) -> TicketOutcome:
        tool_log: list[dict] = []
        facts: dict = {}
        policy_note = ""
        if req.txn_id:
            domain = by_id(req.txn_id)       # a domain plug-in's record, or a card payment
            args = {domain.id_arg if domain else "txn_id": req.txn_id}
            res = lookup(args)
            tool_log.append({"name": domain.tool if domain else "get_transaction", "input": args, "result": res})
            if res["status"] == "error":
                raise ServiceUnavailable("transaction lookup unavailable; no ticket opened")
            if res["status"] in ("not_found", "refused"):   # refused: the security gateway said no
                return TicketOutcome(None, tool_log=tool_log)
            data = res.get("data") or {}
            facts = {k: data[k] for k in (*self.FACT_KEYS, "return_code", "reason_key") if k in data}
            g = data.get("guidance") or {}
            if req.kind == "retry" and g.get("retryable") is False:
                policy_note = (f"Retry policy: {g.get('reason_key')} is not retryable; "
                               f"allowed only: {', '.join(g.get('retry_only_if', [])) or 'none'}")
        ticket, created = open_ticket(self.store, tenant_id=session.tenant_id, session_id=session.session_id,
                                      role=session.role, kind=req.kind, txn_id=req.txn_id,
                                      text=req.text, facts=facts, policy_note=policy_note)
        return TicketOutcome(ticket, created, policy_note, tool_log)
