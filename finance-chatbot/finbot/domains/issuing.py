"""Issued cards: the cards this customer's business hands out, their status, controls and
why a card authorization was declined.

Two records share the prefix: the card itself (card status and controls) carries the
card's state, and each record keeps the last authorization decision with its reason key.
A card reported lost or stolen is never called active, and "unblock it" becomes a ticket
like every other change.

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

import re

from .base import DATE, KEY, WORD, Domain, Rule, money, ok, register

_REPLACE = "A replacement card is the only next step; this card can't be reactivated."

POLICY = {
    "CUSTOMER_FROZEN": Rule("The card was frozen from the app.",
                            "You can unfreeze it yourself in the app's card settings, then try again.",
                            "Frozen by the customer; they can unfreeze it in the app."),
    "LOST": Rule("The card was reported lost.",
                 "A card reported lost can't be reactivated. Order a replacement card in the app.",
                 "Blocked as lost. " + _REPLACE),
    "STOLEN": Rule("The card was reported stolen.",
                   "A card reported stolen can't be reactivated. Order a replacement card in the app.",
                   "Blocked as stolen. " + _REPLACE + " Check recent authorizations for disputes."),
    "FRAUD_SUSPECTED": Rule("The card was blocked after suspected fraud.",
                            "Confirm the recent activity with support so the block can be reviewed.",
                            "Fraud block. The customer must confirm recent activity; lifting it needs a ticket."),
    "EXPIRED": Rule("The card is past its expiry date.",
                    "Activate the replacement card that was sent.",
                    "Expired; a replacement was issued."),
    "NOT_ACTIVATED": Rule("The card has not been activated yet.",
                          "Activate the card in the app before using it.",
                          "Awaiting activation by the customer."),
    # last authorization declines
    "INSUFFICIENT_FUNDS": Rule("The funding account did not have enough available balance.",
                               "Add funds to the funding account, then try the purchase again.",
                               "Funding account balance was too low at authorization time.",
                               True, "The purchase can be tried again once the funding account has the money."),
    "LIMIT_EXCEEDED": Rule("The card's spend limit was reached.",
                           "The purchase can go ahead once the limit allows it. Raising the limit needs an "
                           "admin and becomes a ticket.",
                           "Spend limit reached. A limit change is a ticket for an admin.",
                           False, "Do not advise bypassing the limit.", ("limit", "admin", "ticket")),
    "MCC_BLOCKED": Rule("The merchant category is blocked by the card's controls.",
                        "An admin can change the card's category controls; that needs a ticket.",
                        "Category control block, working as configured. A control change is a ticket.",
                        False, "Do not advise working around the category control.", ("control", "admin", "ticket")),
    "CARD_FROZEN": Rule("The card was frozen when the purchase was tried.",
                        "Unfreeze the card in the app, then try the purchase again.",
                        "Declined because the card was frozen.",
                        True, "The purchase can be tried again once the card is unfrozen."),
    "INCORRECT_PIN": Rule("The PIN was entered incorrectly.",
                          "Try again with the correct PIN; you can view it in the app.",
                          "PIN mismatch. The customer can view the PIN in the app.",
                          True, "The purchase can be tried again with the correct PIN."),
    "EXPIRED_CARD": Rule("The card had expired when the purchase was tried.",
                         "Use the replacement card instead.",
                         "Authorization on an expired card.",
                         False, "Do not retry with this card.", ("replacement",)),
}

CAUSES = {"CUSTOMER_FROZEN": "frozen", "LOST": "lost", "STOLEN": "stolen", "FRAUD_SUSPECTED": "fraud",
          "EXPIRED": "expired_card", "NOT_ACTIVATED": "not_activated", "INSUFFICIENT_FUNDS": "insufficient_funds",
          "LIMIT_EXCEEDED": "limit", "MCC_BLOCKED": "category_block", "CARD_FROZEN": "frozen",
          "INCORRECT_PIN": "pin", "EXPIRED_CARD": "expired_card"}
PHRASES = {
    "frozen": r"\b(card (is|was|has been) frozen|frozen card|freeze on the card)\b",
    "lost": r"\breported lost\b|\blost card\b",
    "stolen": r"\breported stolen\b|\bstolen card\b",
    "not_activated": r"\bnot (been )?activated\b|\bawaiting activation\b",
    "pin": r"\b(incorrect|wrong) pin\b|\bpin (was )?(entered incorrectly|mismatch)\b",
    "category_block": r"\b(merchant category|mcc|category control)\b",
}
_LAST4 = re.compile(r"^\d{4}$")


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    if status != "active" and d.get("status_reason"):
        return str(d["status_reason"])
    last = d.get("last_authorization") or {}
    return str(last.get("reason_key", "")) if isinstance(last, dict) and last.get("result") == "declined" else ""


def _summary(d: dict) -> str | None:
    status, kind = str(d.get("status", "")).lower(), ok(WORD, d.get("form"))
    last4 = ok(_LAST4, d.get("last4"))
    if status not in ISSUING.statuses.values() or not (kind and last4):
        return None
    line = f"Card {d['card_id']} ({kind}, ending {last4}) is {status}."
    if key := ok(KEY, d.get("status_reason")):
        line += f" Status reason: {key}."
    if limit := money(d, "spend_limit"):
        line += f" Spend limit {limit} per month."
    last = d.get("last_authorization") or {}
    if isinstance(last, dict) and last.get("result") == "declined":
        amount, key, when = money({**last, "currency": d.get("currency")}, "amount"), \
            ok(KEY, last.get("reason_key")), ok(DATE, str(last.get("at", ""))[:10])
        if amount and key:
            line += f" Last authorization: declined for {amount}" + (f" on {when}" if when else "") + \
                f" with reason {key}."
    return line


ISSUING = register(Domain(
    name="issuing", noun="card", prefix="card", tool="get_card", id_arg="card_id", source="card_db",
    description=("Fetch the authoritative record for one issued card belonging to the current customer's "
                 "business: form, last4, status and its reason, spend limit, and the last authorization "
                 "decision with its decline reason. Use whenever the user asks about an issued card, a frozen "
                 "or lost card, or why a card purchase was declined. Returns status=not_found if it does not "
                 "exist for this customer."),
    fields=frozenset({"card_id", "form", "last4", "status", "status_reason", "currency", "spend_limit",
                      "spent_this_month", "last_authorization", "issued_on", "guidance"}),
    statuses={"active": "active", "frozen": "frozen", "blocked": "blocked", "expired": "expired",
              "inactive": "inactive", "cancelled": "cancelled", "canceled": "cancelled"},
    open_statuses=frozenset(), policy=POLICY, reason_key=_reason, summary=_summary,
    causes=CAUSES, cause_phrases=PHRASES))
