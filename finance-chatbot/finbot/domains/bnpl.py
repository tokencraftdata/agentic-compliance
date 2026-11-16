"""Buy now, pay later plans: instalments paid and due, autopay failures, late fees.

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

from .base import DATE, Domain, Rule, money, ok, register

POLICY = {
    "INSTALLMENT_MISSED": Rule("An instalment was not paid on its due date.",
                               "Pay the missed instalment in the app. Late fees follow the plan's terms; a new "
                               "schedule is a ticket for the collections team.",
                               "Past due. A rescheduling request is a ticket; do not waive fees in chat."),
    "AUTOPAY_CARD_DECLINED": Rule("The automatic payment for the latest instalment was declined by the card.",
                                  "Update the payment card in the app; the instalment is tried again on the next "
                                  "attempt date on the record.",
                                  "Autopay declined. The customer updates the card; the next attempt is automatic."),
    "PLAN_CANCELLED_REFUND": Rule("The merchant refunded the purchase, so the plan was cancelled.",
                                  "Nothing more is owed on this plan. Instalments already paid are returned to the "
                                  "card used.",
                                  "Cancelled after a merchant refund; paid instalments are returned to the card."),
}

CAUSES = {"INSTALLMENT_MISSED": "delinquency", "AUTOPAY_CARD_DECLINED": "autopay_declined",
          "PLAN_CANCELLED_REFUND": "merchant_refund"}
PHRASES = {
    "autopay_declined": r"\b(autopay|automatic payment)\b[^.]{0,30}\b(declined|failed)\b",
    "merchant_refund": r"\bmerchant refund(ed)?\b|\brefunded by the merchant\b",
}


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    if status == "past_due":
        return "INSTALLMENT_MISSED"
    if status == "cancelled" and d.get("cancel_reason") == "merchant_refund":
        return "PLAN_CANCELLED_REFUND"
    last = d.get("last_payment") or {}
    return "AUTOPAY_CARD_DECLINED" if isinstance(last, dict) and last.get("result") == "declined" else ""


def _summary(d: dict) -> str | None:
    status, purchase = str(d.get("status", "")).lower(), money(d, "purchase_amount")
    total, paid = d.get("installments_total"), d.get("installments_paid")
    if not (purchase and isinstance(total, int) and isinstance(paid, int) and 0 <= paid <= total <= 48):
        return None
    head = f"Plan {d['plan_id']} for a {purchase} purchase: {paid} of {total} instalments paid"
    if status == "paid_off":
        return f"{head}; the plan is paid off."
    if status == "cancelled":
        return f"{head}; the plan is cancelled."
    amount, due = money(d, "next_installment_amount"), ok(DATE, d.get("next_installment_due"))
    if status not in ("active", "past_due") or not (amount and due):
        return None
    if status == "past_due":
        line = f"{head}; the plan is past due. The instalment of {amount} was due {due}."
        if fee := money(d, "late_fee"):
            line += f" Late fee {fee}."
        return line
    line = f"{head}. Next instalment {amount} due {due}."
    last = d.get("last_payment") or {}
    if isinstance(last, dict) and last.get("result") == "declined" and (nxt := ok(DATE, last.get("next_attempt_on"))):
        line += f" The last automatic payment was declined; next attempt on {nxt}."
    return line


BNPL = register(Domain(
    name="bnpl", noun="plan", prefix="bnpl", tool="get_bnpl_plan", id_arg="plan_id", source="bnpl_db",
    description=("Fetch the authoritative record for one buy-now-pay-later plan belonging to the current customer: "
                 "purchase amount, instalments paid and total, the next instalment and due date, late fees, and "
                 "the last automatic payment's result. Use whenever the user asks about a specific plan. Returns "
                 "status=not_found if it does not exist for this customer."),
    fields=frozenset({"plan_id", "status", "currency", "purchase_amount", "installments_total", "installments_paid",
                      "next_installment_amount", "next_installment_due", "late_fee", "last_payment",
                      "cancel_reason", "guidance"}),
    statuses={"active": "active", "past due": "past_due", "overdue": "past_due", "paid off": "paid_off",
              "cancelled": "cancelled", "canceled": "cancelled"},
    open_statuses=frozenset(), policy=POLICY, reason_key=_reason, summary=_summary,
    causes=CAUSES, cause_phrases=PHRASES, status_causes={"past_due": "delinquency"}))
