"""Fixed replies: everything finbot says that is decided by code, not written by the model.

Keeping them in one place means the same situation always gets the same words, and
wording changes are reviewed as text, not hunted for inside control flow.
Templates with a "merchant" and a "support" form are picked with for_role().
"""
from __future__ import annotations

UNAVAILABLE = ("I can't reach our systems right now, so I won't guess. Please try again in a minute or two. "
               "If it's urgent, contact {contact} and quote reference {ref}.")
HANDOFF = {
    "merchant": ("I still can't verify this from our records, so I've flagged it for a support specialist "
                 "(reference {ref}). You can also contact {contact} and quote that reference."),
    "support": ("This was unverifiable twice in a row: escalate to tier 2 with reference {ref}. "
                "The session record has the full context."),
}
ESCALATION = {
    "merchant": ("This needs a person rather than a bot, so I've passed it to a support specialist "
                 "(reference {ref}). You can also contact {contact} and quote that reference."),
    "support": "Route this to a human: it matches the escalation policy ({why}). Reference {ref}.",
}
RISK_LOCK = ("I've paused this conversation and passed it to a support specialist (reference {ref}). "
             "Please contact {contact} and quote that reference.")
TICKET = {
    "merchant": ("I can't make changes myself, so I've opened ticket {id} asking our team to {action} {target}. "
                 "A person will review it and carry it out; nothing has been changed yet. "
                 "Quote {id} if you contact {contact}."),
    "support": "Ticket {id} opened for manual execution: {kind} on {target}. Nothing has been executed.",
}
TICKET_RETRY_POLICY = {
    "merchant": " Note: our retry policy says this payment shouldn't be retried as is; the team will check.",
    "support": " {note}.",
}
TICKET_EXISTS = " This request was already open as {id}, so no duplicate was created."
TICKET_NOT_FOUND = ("I couldn't find {txn} for your account, so I haven't opened a ticket. "
                    "Please check the transaction ID.")
CARD_NOTICE = ("I removed card details from your message. Please don't share card numbers, security codes "
               "or PINs here: we never need them.")


def for_role(templates: dict[str, str], role: str) -> str:
    """Merchants get the customer-facing form; support staff and everyone else the internal one."""
    return templates["merchant" if role == "merchant" else "support"]
