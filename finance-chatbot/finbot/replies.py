"""Fixed replies: everything finbot says that is decided by code, not written by the model.

Keeping them in one place means the same situation always gets the same words, and
wording changes are reviewed as text, not hunted for inside control flow.
Templates with a "customer" and a "support" form are picked with for_role()
("customer" covers every customer-facing role, the legacy "merchant" included).
"""
from __future__ import annotations

NOT_ALLOWED = ("I can't look that up for you, so I haven't opened a request. A support specialist can help "
               "if you need this.")

CANCELLED = "Stopped. I didn't finish that request, and nothing was changed."

UNAVAILABLE = ("I can't reach our systems right now, so I won't guess. Please try again in a minute or two. "
               "If it's urgent, contact {contact} and quote reference {ref}.")
HANDOFF = {
    "customer": ("I still can't verify this from our records, so I've flagged it for a support specialist "
                 "(reference {ref}). You can also contact {contact} and quote that reference."),
    "support": ("This was unverifiable twice in a row: escalate to tier 2 with reference {ref}. "
                "The session record has the full context."),
}
ESCALATION = {
    "customer": ("This needs a person rather than a bot, so I've passed it to a support specialist "
                 "(reference {ref}). You can also contact {contact} and quote that reference."),
    "support": "Route this to a human: it matches the escalation policy ({why}). Reference {ref}.",
}
RISK_LOCK = ("I've paused this conversation and passed it to a support specialist (reference {ref}). "
             "Please contact {contact} and quote that reference.")
TICKET = {
    "customer": ("I can't make changes myself, so I've opened ticket {id} asking our team to {action} {target}. "
                 "A person will review it and carry it out; nothing has been changed yet. "
                 "Quote {id} if you contact {contact}."),
    "support": "Ticket {id} opened for manual execution: {kind} on {target}. Nothing has been executed.",
}
TICKET_RETRY_POLICY = {
    "customer": " Note: our retry policy says this payment shouldn't be retried as is; the team will check.",
    "support": " {note}.",
}
TICKET_EXISTS = " This request was already open as {id}, so no duplicate was created."
TICKET_NOT_FOUND = ("I couldn't find {txn} for your account, so I haven't opened a ticket. "
                    "Please check the transaction ID.")
RECORD_ONLY = "I couldn't verify a fuller explanation, so here is only what the record shows."
RECORD_NOT_FOUND_ANY = "I couldn't find {id} for your account. Please check the ID."
RECORD_NOT_FOUND = "I couldn't find {txn} for your account. Please check the transaction ID."
STALE_SOURCE = " Note: the help page cited was last updated on {date} and may be out of date."
ENGLISH_ONLY = {
    "es": "Lo siento, por ahora solo puedo ayudar en inglés. He pasado tu consulta a un especialista de soporte "
          "(referencia {ref}).",
    "fr": "Désolé, je ne peux aider qu'en anglais pour le moment. J'ai transmis votre demande à un spécialiste "
          "du support (référence {ref}).",
    "de": "Entschuldigung, ich kann derzeit nur auf Englisch helfen. Ich habe Ihre Anfrage an einen "
          "Support-Spezialisten weitergegeben (Referenz {ref}).",
    "pt": "Desculpe, por enquanto só posso ajudar em inglês. Passei o seu pedido a um especialista de suporte "
          "(referência {ref}).",
}
ENGLISH_ONLY_EN = ("I can only help in English for now, so I've passed this to a support specialist "
                   "(reference {ref}). You can also contact {contact} and quote that reference.")
CARD_NOTICE = ("I removed card details from your message. Please don't share card numbers, security codes "
               "or PINs here: we never need them.")


def for_role(templates: dict[str, str], role: str) -> str:
    """Support staff get the internal form; customers (and the legacy "merchant" role) the other."""
    return templates["support" if role == "support" else "customer"]
