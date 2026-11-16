"""Loans and credit lines: application decisions, balance, rate, next payment.

The bot never predicts a decision, never quotes a rate that is not on the record and never
changes terms. The credit score and internal grade are not in `fields`, so the model
never sees them.

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

from .base import DATE, KEY, RATE, WORD, Domain, Rule, money, ok, register

_LETTER = "You can ask for the decision letter, which lists the reasons."
_STAFF = "Send the decision letter on request. Do not suggest that an appeal or another application will succeed."
_WAIT = "Do not reapply within 30 days; a new application is assessed from scratch."

POLICY = {
    "DTI_HIGH": Rule("Existing debt was too high compared with income for this product.",
                     _LETTER + " Paying down existing debt improves this ratio.", _STAFF, False, _WAIT, ("30 days",)),
    "CREDIT_HISTORY_SHORT": Rule("The credit history was shorter than this product requires.",
                                 _LETTER, _STAFF, False, _WAIT, ("30 days",)),
    "RECENT_DELINQUENCY": Rule("Recent late payments were on record.", _LETTER, _STAFF, False, _WAIT, ("30 days",)),
    "REVENUE_TOO_LOW": Rule("Business revenue was lower than the minimum for this product.",
                            _LETTER, _STAFF, False, _WAIT, ("30 days",)),
    "INCOME_UNVERIFIED": Rule("Income could not be verified from the documents provided.",
                              "Upload recent income documents, then apply again.",
                              "Declined for missing income evidence. A new application with documents is allowed.",
                              True, "You can reapply once recent income documents are uploaded."),
    "PAST_DUE": Rule("A scheduled payment has not been received.",
                     "Make the overdue payment, or ask support about a payment plan. Late fees follow the "
                     "loan agreement.",
                     "Past due. Offer the payment-plan process; do not waive fees or promise terms in chat."),
    "IN_REVIEW": Rule("The application is still being assessed.",
                      "No action is needed now. A decision can't be predicted in chat.",
                      "In underwriting. Do not predict the decision."),
}

CAUSES = {"DTI_HIGH": "debt_to_income", "CREDIT_HISTORY_SHORT": "credit_history",
          "RECENT_DELINQUENCY": "delinquency", "REVENUE_TOO_LOW": "revenue", "INCOME_UNVERIFIED": "income",
          "PAST_DUE": "delinquency", "IN_REVIEW": "review"}
PHRASES = {
    "debt_to_income": r"\bdebt[- ]to[- ]income\b|\b(existing )?debt was too high\b|\btoo much (existing )?debt\b",
    "credit_history": r"\b(short|limited|thin|not enough) credit history\b|\bcredit history was shorter\b",
    "delinquency": r"\b(missed|late) payments?\b|\bdelinquen\w+\b|\bpayment has not been received\b",
    "revenue": r"\b(low|insufficient|not enough) revenue\b|\brevenue was lower\b",
    "income": r"\bincome (could not|couldn't|was not|wasn't) (be )?verified\b|\bunverified income\b",
    # The score is never in a record the model can see: stating it as the reason is a guess.
    "credit_score": r"\bcredit score\b",
}
_APPLICATION = {"in_review", "declined", "approved"}


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    reasons = d.get("decision_reasons") or []
    if status == "declined" and reasons:
        return str(reasons[0])
    return {"past_due": "PAST_DUE", "in_review": "IN_REVIEW"}.get(status, "")


def _summary(d: dict) -> str | None:
    status, product = str(d.get("status", "")).lower(), ok(WORD, d.get("product"))
    if not product:
        return None
    lid = d["loan_id"]
    if status in _APPLICATION:
        amount = money(d, "principal")
        if not amount:
            return None
        head = f"Loan application {lid} ({product}) for {amount}"
        if status == "in_review":
            return f"{head} is in review; no decision has been made yet."
        done = ok(DATE, d.get("decided_on"))
        if status == "declined":
            keys = [k for k in d.get("decision_reasons") or [] if ok(KEY, k)]
            if not keys:
                return None
            return f"{head} was declined" + (f" on {done}" if done else "") + f". Reasons on record: {', '.join(keys)}."
        rate = ok(RATE, d.get("apr"))
        return f"{head} was approved" + (f" on {done}" if done else "") + (f" at {rate} APR." if rate else ".")
    if status == "paid_off":
        return f"Loan {lid} ({product}) is paid off."
    balance, rate = money(d, "outstanding_balance"), ok(RATE, d.get("apr"))
    payment, due = money(d, "next_payment_amount"), ok(DATE, d.get("next_payment_due"))
    if status not in ("active", "past_due") or not (balance and rate and payment and due):
        return None
    if status == "active":
        return (f"Loan {lid} ({product}) is active. Outstanding balance {balance} at {rate} APR. "
                f"Next payment {payment} due {due}.")
    days = d.get("days_past_due")
    late = f" by {days} days" if isinstance(days, int) and 0 < days < 10000 else ""
    return (f"Loan {lid} ({product}) is past due{late}. Outstanding balance {balance} at {rate} APR. "
            f"The payment of {payment} was due {due}.")


LENDING = register(Domain(
    name="lending", noun="loan", prefix="loan", tool="get_loan", id_arg="loan_id", source="loan_db",
    description=("Fetch the authoritative record for one loan, credit line or loan application belonging to the "
                 "current customer: product, status, amount, rate, outstanding balance, next payment and its due "
                 "date, and the recorded reasons for a declined application. Use whenever the user asks about a "
                 "specific loan or application. Returns status=not_found if it does not exist for this customer."),
    fields=frozenset({"loan_id", "product", "status", "principal", "currency", "apr", "term_months",
                      "outstanding_balance", "next_payment_amount", "next_payment_due", "days_past_due",
                      "decision_reasons", "decided_on", "opened_on", "guidance"}),
    statuses={"approved": "approved", "declined": "declined", "denied": "declined", "rejected": "declined",
              "in review": "in_review", "under review": "in_review", "active": "active", "past due": "past_due",
              "overdue": "past_due", "paid off": "paid_off"},
    open_statuses=frozenset({"in_review"}), policy=POLICY, reason_key=_reason, summary=_summary,
    causes=CAUSES, cause_phrases=PHRASES,
    implied={s: frozenset({"approved"}) for s in ("active", "past_due", "paid_off")},
    status_causes={"past_due": "delinquency"}))
