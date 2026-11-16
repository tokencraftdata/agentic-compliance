"""Subscription billing: plan, status, the current period, failed renewals and retries.

The costly wrong answers are an invented policy and a wrong state: "your subscription was
cancelled" when it is set to cancel at the end of the period, or a refund rule nobody wrote.
State comes from the record; any policy answer must cite a help page (citation check).

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

from .base import DATE, KEY, WORD, Domain, Rule, money, ok, register

POLICY = {
    "INVOICE_CARD_DECLINED": Rule("The renewal payment was declined by the card on file.",
                                  "Update the card; the renewal is tried again automatically on the next attempt "
                                  "date on the record.",
                                  "Renewal declined. Card update triggers the next attempt; no manual retry in chat.",
                                  True, "The renewal is retried automatically on the next attempt date."),
    "INVOICE_CARD_EXPIRED": Rule("The card on file had expired at renewal.",
                                 "Add a current card; the renewal is tried again automatically.",
                                 "Expired card at renewal; the customer adds a new card.",
                                 True, "The renewal is retried automatically once a current card is added."),
    "CANCEL_AT_PERIOD_END": Rule("The subscription is set to cancel at the end of the current period.",
                                 "It stays active until the period end date on the record. You can undo the "
                                 "cancellation before then in account settings.",
                                 "Scheduled cancellation; still active until period end."),
}

CAUSES = {"INVOICE_CARD_DECLINED": "invoice_declined", "INVOICE_CARD_EXPIRED": "expired_card",
          "CANCEL_AT_PERIOD_END": "scheduled_cancellation"}
PHRASES = {
    "invoice_declined": r"\b(renewal|invoice|subscription) (payment )?(was |has been )?declined\b",
    "scheduled_cancellation": r"\bcancel\w* at the end of\b|\bscheduled to cancel\b",
}


def _reason(d: dict) -> str:
    inv = d.get("last_invoice") or {}
    if isinstance(inv, dict) and inv.get("status") == "failed":
        return str(inv.get("reason_key") or "")
    return "CANCEL_AT_PERIOD_END" if d.get("cancel_at_period_end") is True else ""


def _summary(d: dict) -> str | None:
    status, plan, price = str(d.get("status", "")).lower(), ok(WORD, d.get("plan")), money(d, "price")
    interval = ok(WORD, d.get("interval"))
    end = ok(DATE, d.get("current_period_end"))
    if not (plan and price and interval) or status not in SUBSCRIPTIONS.statuses.values():
        return None
    line = f"Subscription {d['subscription_id']} ({plan}, {price} per {interval}) is {status.replace('_', ' ')}."
    if end:
        line += f" The current period ends {end}."
    if d.get("cancel_at_period_end") is True and end:
        line += f" It is set to cancel at the end of the period, on {end}."
    inv = d.get("last_invoice") or {}
    if isinstance(inv, dict) and inv.get("status") == "failed":
        amount = money({**inv, "currency": d.get("currency")}, "amount")
        key, nxt = ok(KEY, inv.get("reason_key")), ok(DATE, inv.get("next_attempt_on"))
        if amount and key:
            line += f" The last invoice of {amount} failed with reason {key}"
            line += f"; next attempt on {nxt}." if nxt else "."
    return line


SUBSCRIPTIONS = register(Domain(
    name="subscriptions", noun="subscription", prefix="sub", tool="get_subscription", id_arg="subscription_id",
    source="billing_db",
    description=("Fetch the authoritative billing record for one subscription belonging to the current customer: "
                 "plan, price and interval, status, current period end, whether it is set to cancel at period end, "
                 "and the last invoice's result and next retry date. Use whenever the user asks about a specific "
                 "subscription. Returns status=not_found if it does not exist for this customer."),
    fields=frozenset({"subscription_id", "plan", "status", "currency", "price", "interval", "current_period_end",
                      "cancel_at_period_end", "last_invoice", "guidance"}),
    statuses={"active": "active", "past due": "past_due", "cancelled": "cancelled", "canceled": "cancelled",
              "paused": "paused", "trialing": "trialing", "on trial": "trialing"},
    open_statuses=frozenset(), policy=POLICY, reason_key=_reason, summary=_summary,
    causes=CAUSES, cause_phrases=PHRASES))
