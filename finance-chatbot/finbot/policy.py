"""Payment retry policy and next-step routing, decided in code, not by the model.

For every failed or uncertain transaction, get_transaction attaches a
`guidance` object computed here: whether a retry is allowed, the retry rule,
and the next step for the person asking (merchant, support agent, or the
cardholder the merchant should talk to). The model phrases it; the evidence
guard blocks answers that contradict it.

The rules below are an EXAMPLE policy matching the synthetic knowledge base
(soft declines: at most one retry after 1 hour, never more than 4 in 16 days;
hard declines: never retry the same card details). Replace them with your
acquirer's and the card networks' current reattempt rules before production.
"""
from __future__ import annotations

SOFT_RULE = "Retry at most once after 1 hour, and never more than 4 times in 16 days."
HARD_RULE = "Do not retry with the same card details."

# key: decline code or gateway error
# (retryable, rule, cardholder action, merchant action, support action)
POLICY: dict[str, tuple[bool, str, str, str, str]] = {
    "05": (False, HARD_RULE, "Contact the card's issuing bank, or pay with another card.",
           "Ask the customer to use another card or call their bank. Don't retry.",
           "Issuer declined without a reason; nothing to fix on our side. Advise the merchant not to retry."),
    "51": (True, SOFT_RULE, "Use another card, or retry once funds are available.",
           "Offer another payment method; one retry after 1 hour is allowed.",
           "Soft decline. A single delayed retry is allowed under policy."),
    "54": (False, HARD_RULE, "Update the card's expiry date or use a new card.",
           "Ask the customer for updated card details.",
           "Expired card; the merchant needs updated card details."),
    "14": (False, HARD_RULE, "Re-enter the card number carefully.",
           "Ask the customer to re-enter the card number.",
           "Invalid card number; data-entry issue on the customer side."),
    "61": (True, SOFT_RULE, "Ask the bank to raise the limit, or split the purchase.",
           "Suggest a smaller amount or another card; one delayed retry is allowed.",
           "Limit exceeded. A single delayed retry is allowed."),
    "65": (True, SOFT_RULE, "Wait and retry later, or contact the bank.",
           "Retry later (after at least 1 hour) or offer another card.",
           "Frequency limit. A single delayed retry is allowed."),
    "N7": (False, HARD_RULE, "Re-enter the card's security code.",
           "Ask the customer to re-enter the CVV; submit as a new payment.",
           "CVV mismatch; the customer must re-enter it."),
    "59": (False, HARD_RULE, "Contact the bank to verify the purchase.",
           "Don't retry. The customer must verify with their bank first.",
           "Issuer suspects fraud. Do not retry; consider a risk review if it repeats."),
    "91": (True, "Retry after a few minutes (at most 3 attempts in 1 hour).", "Try again in a few minutes.",
           "Retry in a few minutes; the issuer was temporarily unavailable.",
           "Issuer outage. Short delayed retries are allowed."),
    "R1": (False, HARD_RULE + " Stop the recurring schedule.", "No action unless you want to restart the subscription.",
           "Stop recurring charges on this card and ask for new consent.",
           "Cardholder revoked authorization. Recurring retries must stop."),
    "RISK_BLOCKED": (False, "Do not retry until the merchant's risk settings are reviewed.",
                     "Contact the merchant.",
                     "Review your risk rules in the dashboard or contact support. Don't retry the same payment.",
                     "Blocked by the merchant's own risk rules. Don't disclose thresholds; route to a risk review."),
    "3DS_FAILED": (True, "Retry once, with the customer completing their bank's verification step.",
                   "Try again and complete the bank's verification (app or SMS code).",
                   "Ask the customer to retry and complete 3-D Secure.",
                   "Customer abandoned or failed 3-D Secure; a customer-driven retry is fine."),
    "AVS_MISMATCH": (True, "Retry after correcting the billing address.",
                     "Check the billing address matches the card statement.",
                     "Ask the customer to correct the billing address, then retry.",
                     "Address mismatch; retry after the address is fixed."),
    "PROCESSOR_TIMEOUT": (False, "Do NOT retry yet: check the transaction status first. If it never reached "
                                 "the processor, retry with the SAME idempotency key.",
                          "Wait before paying again; you might otherwise be charged twice.",
                          "Check the payment status before retrying; reuse the same idempotency key.",
                          "Outcome unknown. Confirm the status with the processor before any retry."),
    "PROCESSOR_ERROR": (True, "Retry after a few minutes with the same idempotency key. The processor returned an "
                              "error before the request reached the card network, so nothing was charged.",
                        "Try again in a few minutes; you were not charged.",
                        "Retry in a few minutes with the same idempotency key; nothing was charged.",
                        "Processor-side failure before the network; the cardholder was not charged."),
    "NO_ROUTE": (True, "Retry later, or use another card: no processor could accept this card and currency then.",
                 "Try again later or use another card.",
                 "Retry later or offer another card; no processor could take this card and currency at the time.",
                 "No eligible processor (capability or outage). Check processor health before advising."),
    "DUPLICATE_REQUEST": (False, "Do not resend. Use a new idempotency key only for a genuinely new payment.",
                          "No action needed.",
                          "The same idempotency key was reused with different details; fix the integration.",
                          "Integration bug on the merchant side (idempotency key reuse)."),
}

UNKNOWN = (False, "Do not retry until the cause is understood.", "Contact the merchant.",
           "Contact support with the transaction ID.", "Unmapped failure; investigate the logs.")

# For non-retryable failures, retry advice is acceptable only together with one
# of these qualifiers (e.g. "retry WITH ANOTHER CARD" is fine for a hard decline).
HARD_QUALIFIERS = ["another card", "different card", "new card", "other card", "updated card",
                   "another payment method", "different payment method", "re-enter", "reenter", "correct"]
QUALIFIERS = {"PROCESSOR_TIMEOUT": ["status", "idempotency"], "RISK_BLOCKED": ["review", "risk"],
              "R1": ["consent"], "59": ["verif"], "DUPLICATE_REQUEST": ["new payment"]}


def guidance(txn: dict, role: str) -> dict | None:
    """Return retry guidance for a non-approved transaction, or None if approved."""
    status = str(txn.get("status", "")).lower()
    if status in ("approved", "succeeded", "settled"):
        return None
    key = txn.get("decline_code") or txn.get("error") or ""
    retryable, rule, cardholder, merchant, support = POLICY.get(key, UNKNOWN)
    next_step = merchant if role == "merchant" else support
    out = {"reason_key": key or "unknown", "retryable": retryable, "retry_rule": rule,
           "next_step": next_step, "tell_the_cardholder": cardholder}
    if not retryable:
        out["retry_only_if"] = QUALIFIERS.get(key, HARD_QUALIFIERS)
    return out
