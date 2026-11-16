"""Disputes and chargebacks: stage, reason, the evidence the card network expects, the deadline.

The costly wrong answers here are a wrong deadline (a late response loses the dispute
automatically) and "you've won" while the network is still deciding. The deadline is only
ever quoted from the record, and an undecided dispute is never described as won or lost.

The rules are an EXAMPLE policy that matches the synthetic help pages. Network reason codes
are shown as readable categories; map them to your networks' current codes.
"""
from __future__ import annotations

from .base import DATE, KEY, Domain, Rule, money, ok, register

_DEADLINE = " Submit it before the respond-by date on the record; a late response is lost automatically."

POLICY = {
    "FRAUD_CARD_ABSENT": Rule("The cardholder says they did not make this online or phone purchase.",
                              "Submit the card authentication result, address check, device data and delivery "
                              "proof." + _DEADLINE,
                              "Fraud dispute. Evidence: authentication result, address check, device data, "
                              "delivery proof." + _DEADLINE),
    "PRODUCT_NOT_RECEIVED": Rule("The cardholder says the goods or service never arrived.",
                                 "Submit tracking and proof of delivery." + _DEADLINE,
                                 "Not-received dispute. Evidence: tracking and delivery proof." + _DEADLINE),
    "NOT_AS_DESCRIBED": Rule("The cardholder says what arrived was significantly different from what was sold.",
                             "Submit the product description, photos and any messages with the customer." + _DEADLINE,
                             "Quality dispute. Evidence: listing, photos, correspondence." + _DEADLINE),
    "SUBSCRIPTION_CANCELLED": Rule("The cardholder says they cancelled the subscription before this charge.",
                                   "Submit the cancellation policy and the account's cancellation log." + _DEADLINE,
                                   "Recurring-charge dispute. Evidence: cancellation policy and logs." + _DEADLINE),
    "DUPLICATE_CHARGE": Rule("The cardholder says they were charged twice for one purchase.",
                             "Show that the two charges were separate purchases, or accept the dispute." + _DEADLINE,
                             "Duplicate-charge dispute. Evidence: two distinct orders, or accept." + _DEADLINE),
    "CREDIT_NOT_PROCESSED": Rule("The cardholder says a promised refund was never processed.",
                                 "Submit the refund record, or the policy that shows no refund was due." + _DEADLINE,
                                 "Credit-not-processed dispute. Evidence: refund record or policy." + _DEADLINE),
    "DISPUTE_UNDER_REVIEW": Rule("Your evidence was submitted and the card network is deciding.",
                                 "No action is needed now. The outcome can't be predicted in chat.",
                                 "Representment submitted. Do not predict the outcome."),
    "DISPUTE_LOST": Rule("The card network decided for the cardholder.",
                         "The disputed amount stays with the cardholder. A second-round appeal is a ticket for "
                         "the disputes team, who decide whether it is possible.",
                         "Lost. A pre-arbitration request is a ticket; do not promise it is possible."),
}

CAUSES = {"FRAUD_CARD_ABSENT": "fraud", "PRODUCT_NOT_RECEIVED": "not_received", "NOT_AS_DESCRIBED": "not_as_described",
          "SUBSCRIPTION_CANCELLED": "revoked", "DUPLICATE_CHARGE": "duplicate",
          "CREDIT_NOT_PROCESSED": "refund_not_processed", "DISPUTE_UNDER_REVIEW": "review", "DISPUTE_LOST": "lost"}
PHRASES = {
    "not_received": r"\b(product|goods|item|order|package)s? (was |were )?(not received|never (arrived|received|"
                    r"delivered))\b|\bproduct not received\b",
    "not_as_described": r"\bnot as described\b|\bsignificantly different\b",
    "refund_not_processed": r"\b(refund|credit) (was |has )?(not|never) (been )?(processed|issued)\b",
    "duplicate": r"\bcharged twice\b|\bduplicate charge\b",
}


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    if status == "under_review":
        return "DISPUTE_UNDER_REVIEW"
    if status == "lost":
        return "DISPUTE_LOST"
    return str(d.get("reason_code") or "") if status == "needs_response" else ""


def _summary(d: dict) -> str | None:
    status, amount, key = str(d.get("status", "")).lower(), money(d, "amount"), ok(KEY, d.get("reason_code"))
    if not (amount and key):
        return None
    head = f"Dispute {d['dispute_id']} for {amount}, reason {key},"
    if status == "needs_response":
        due = ok(DATE, d.get("respond_by"))
        return f"{head} needs your response" + (f" by {due}." if due else ".") if due else None
    if status == "under_review":
        return f"{head} is under review by the card network; no decision has been made yet."
    if status in ("won", "lost", "accepted"):
        done = ok(DATE, d.get("decided_on"))
        word = {"won": "was won", "lost": "was lost", "accepted": "was accepted by you"}[status]
        return f"{head} {word}" + (f" on {done}." if done else ".")
    return None


DISPUTES = register(Domain(
    name="disputes", noun="dispute", prefix="dsp", tool="get_dispute", id_arg="dispute_id", source="dispute_db",
    description=("Fetch the authoritative record for one card dispute (chargeback) against the current customer: "
                 "stage, reason category, amount, the respond-by deadline, whether evidence was submitted, and the "
                 "outcome. Use whenever the user asks about a specific dispute. Returns status=not_found if it "
                 "does not exist for this customer."),
    fields=frozenset({"dispute_id", "status", "reason_code", "amount", "currency", "respond_by", "opened_on",
                      "evidence_submitted", "decided_on", "guidance"}),
    statuses={"won": "won", "lost": "lost", "accepted": "accepted", "under review": "under_review",
              "in review": "under_review"},
    open_statuses=frozenset({"needs_response", "under_review"}), policy=POLICY, reason_key=_reason,
    summary=_summary, causes=CAUSES, cause_phrases=PHRASES))
