"""Mortgage servicing: balance, rate, next payment, escrow, delinquency, payoff quotes.

The bot quotes a payoff only from a payoff quote on the record (with its good-through date),
never predicts the outcome of a loss-mitigation review, and never discusses foreclosure:
that is for a person, so any mention of it is treated as a guess the record can't support.

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

from .base import DATE, RATE, WORD, Domain, Rule, money, ok, register

POLICY = {
    "PAYMENT_PAST_DUE": Rule("A scheduled payment has not been received.",
                             "Make the overdue payment, or ask about assistance options. A repayment plan or "
                             "modification is decided by a person, not in chat.",
                             "Delinquent. Offer the assistance-options process; do not discuss foreclosure or "
                             "promise any plan."),
    "ESCROW_SHORTAGE": Rule("The escrow account will not cover the coming tax and insurance bills.",
                            "You can pay the shortage at once, or spread it over the next 12 payments.",
                            "Escrow shortage from the annual analysis; the customer chooses lump sum or spread."),
    "FORBEARANCE_ACTIVE": Rule("Payments are paused or reduced under a forbearance plan.",
                               "Follow the plan's terms. What happens when it ends is decided before its end date.",
                               "In forbearance. The exit option is decided by the loss-mitigation team."),
    "LOSS_MIT_REVIEW": Rule("An assistance application is being reviewed.",
                            "No action is needed now. The outcome can't be predicted in chat.",
                            "Loss-mitigation review open. Do not predict the outcome or a date."),
}

CAUSES = {"PAYMENT_PAST_DUE": "delinquency", "ESCROW_SHORTAGE": "escrow_shortage",
          "FORBEARANCE_ACTIVE": "forbearance", "LOSS_MIT_REVIEW": "review"}
PHRASES = {
    "escrow_shortage": r"\bescrow (shortage|shortfall)\b|\bshort(age|fall) in (your |the )?escrow\b",
    "forbearance": r"\bforbearance\b",
    # Never on a record the model sees, so always a guess.
    "foreclosure": r"\bforeclos\w*\b",
}


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    if d.get("loss_mitigation") == "in_review":
        return "LOSS_MIT_REVIEW"
    if status == "delinquent":
        return "PAYMENT_PAST_DUE"
    if status == "forbearance":
        return "FORBEARANCE_ACTIVE"
    return "ESCROW_SHORTAGE" if money(d, "escrow_shortage") else ""


def _summary(d: dict) -> str | None:
    status, product = str(d.get("status", "")).lower(), ok(WORD, d.get("product"))
    if not product:
        return None
    mid = d["mortgage_id"]
    if status == "paid_off":
        return f"Mortgage {mid} ({product}) is paid off."
    balance, rate = money(d, "principal_balance"), ok(RATE, d.get("rate"))
    payment, due = money(d, "next_payment_amount"), ok(DATE, d.get("next_payment_due"))
    if status not in ("current", "delinquent", "forbearance") or not (balance and rate and payment and due):
        return None
    word = {"current": "is current", "delinquent": "is delinquent", "forbearance": "is in forbearance"}[status]
    line = f"Mortgage {mid} ({product}) {word}. Principal balance {balance} at {rate}. "
    line += (f"The payment of {payment} was due {due}." if status == "delinquent"
             else f"Next payment {payment} due {due}.")
    if shortage := money(d, "escrow_shortage"):
        line += f" Escrow shortage {shortage}."
    quote, through = money(d, "payoff_quote_amount"), ok(DATE, d.get("payoff_good_through"))
    if quote and through:
        line += f" Payoff quote {quote}, good through {through}."
    return line


MORTGAGES = register(Domain(
    name="mortgages", noun="mortgage", prefix="mtg", tool="get_mortgage", id_arg="mortgage_id",
    source="mortgage_db",
    description=("Fetch the authoritative servicing record for one mortgage belonging to the current customer: "
                 "status, principal balance, rate, next payment and due date, escrow shortage, and a payoff quote "
                 "with its good-through date if one was requested. Use whenever the user asks about a specific "
                 "mortgage. Returns status=not_found if it does not exist for this customer."),
    fields=frozenset({"mortgage_id", "product", "status", "currency", "principal_balance", "rate",
                      "next_payment_amount", "next_payment_due", "escrow_shortage", "days_past_due",
                      "loss_mitigation", "payoff_quote_amount", "payoff_good_through", "guidance"}),
    statuses={"current": "current", "delinquent": "delinquent", "past due": "delinquent",
              "in forbearance": "forbearance", "paid off": "paid_off"},
    open_statuses=frozenset(), policy=POLICY, reason_key=_reason, summary=_summary,
    causes=CAUSES, cause_phrases=PHRASES,
    status_causes={"delinquent": "delinquency", "forbearance": "forbearance"}))
