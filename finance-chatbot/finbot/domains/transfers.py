"""Money movement: bank transfers on batch (ach), real-time (rtp) and wire rails.

Return codes follow the familiar R-code style of bank debit returns; real-time and wire
rejects use scheme-style keys. What makes the rails different is written into the policy,
not the prompt: a settled real-time or wire payment is irrevocable, so "cancel it" is
answered with the recall process, and only a rejected real-time payment may be sent again.
The rules are an EXAMPLE policy that matches the synthetic help pages. Replace them with
your bank partner's and the payment scheme's current rules before production.
"""
from __future__ import annotations

import re

from .base import DATE, KEY, WORD, Domain, Rule, money, ok, register

_NEW_DETAILS = ("new account", "different account", "another account", "updated account", "correct", "new transfer")
_NO = "Do not resend it to the same account."

POLICY = {
    "R01": Rule("The paying account did not have enough money when the transfer was presented.",
                "Make sure the account has the money, then send it again.",
                "Soft return. Sending again is allowed within the limit in the policy.",
                True, "Send it again at most 2 times, and only within 180 days of the first attempt."),
    "R02": Rule("The receiving account is closed.",
                "Ask the recipient for new account details, then create a new transfer.",
                "Hard return. The customer needs new account details from the recipient.",
                False, _NO, _NEW_DETAILS),
    "R03": Rule("The receiving bank found no account matching these details.",
                "Check the account details with the recipient, then create a new transfer.",
                "Hard return. The account details are wrong; the customer must correct them.",
                False, _NO, _NEW_DETAILS),
    "R04": Rule("The account number is not valid.",
                "Correct the account number, then create a new transfer.",
                "Hard return. Invalid account number; the customer must correct it.",
                False, _NO, _NEW_DETAILS),
    "R08": Rule("The account holder asked their bank to stop this payment.",
                "Contact the account holder. Do not resend it unless they agree.",
                "Stop order by the account holder. Sending again needs their agreement.",
                False, "Do not resend it unless the account holder agrees.", ("agree", "agreement")),
    "R10": Rule("The account holder told their bank the debit was not authorized.",
                "Do not resend it. A new written authorization from the account holder is needed first.",
                "Unauthorized return. No further debit without a new written authorization.",
                False, "Do not resend it without a new written authorization.", ("authorization",)),
    "R16": Rule("The receiving account is frozen.",
                "Ask the recipient to contact their bank, or use another account.",
                "Hard return. The receiving account is frozen at its bank.",
                False, _NO, _NEW_DETAILS),
    "DAILY_LIMIT_EXCEEDED": Rule("The transfer was over the daily transfer limit.",
                                 "Send it again on the next business day, or send a smaller amount.",
                                 "Over the daily limit. A limit change needs a ticket, not chat.",
                                 True, "Send it again on the next business day, or split it below the limit."),
    "COMPLIANCE_HOLD": Rule("The transfer is under review before it is released.",
                            "No action is needed now. Do not resend it: a second transfer could be paid as well. "
                            "We can't share details of a review.",
                            "Held for review. Do not disclose or guess the reason, and do not suggest "
                            "resending.",
                            False, "Do not resend it while the first transfer is under review."),
    "INVALID_ROUTING_NUMBER": Rule("The bank routing number is not valid.",
                                   "Correct the routing number, then create a new transfer.",
                                   "Rejected before it was sent: invalid routing number.",
                                   False, _NO, _NEW_DETAILS),
    "RTP_REJECTED_BY_RECEIVING_BANK": Rule(
        "The receiving bank rejected this real-time payment.",
        "Check the account details with the recipient. You can send it again once they are confirmed.",
        "Real-time reject by the receiving bank. A corrected payment may be sent.",
        True, "Send it again only after the recipient confirms the account details."),
    "RTP_NOT_ENABLED": Rule("The receiving account cannot receive real-time payments.",
                            "Send it as a standard transfer instead, or ask the recipient for an account that "
                            "accepts instant payments.",
                            "Receiver not reachable on the real-time rail. A standard transfer will work.",
                            False, "Do not resend it on the real-time rail.",
                            ("standard", "another rail", "ach", "different account")),
    "SETTLED_IRREVOCABLE": Rule("This payment settled instantly and cannot be cancelled or recalled "
                                "automatically.",
                                "If it went to the wrong place, ask support to request a return from the "
                                "receiving bank. The receiving bank does not have to agree.",
                                "Settled and irrevocable. A return request to the receiving bank is the only "
                                "path, and it can be refused; open it as a ticket.",
                                False, "Do not send it again: the first payment has already settled."),
    "WIRE_RECALLED": Rule("A recall was requested for this wire.",
                          "The receiving bank is reviewing the recall. We'll update you; the funds are not "
                          "back until it agrees.",
                          "Recall pending with the receiving bank. Do not promise the funds will come back."),
    "BENEFICIARY_NAME_MISMATCH": Rule("The recipient name does not match the name on the receiving account.",
                                      "Check the recipient's name with them, then create a new transfer.",
                                      "Rejected by the name check. The customer must confirm the recipient's name.",
                                      False, _NO, _NEW_DETAILS),
}

CAUSES = {"RTP_REJECTED_BY_RECEIVING_BANK": "rejected_by_bank", "RTP_NOT_ENABLED": "rail_unreachable",
          "SETTLED_IRREVOCABLE": "irrevocable", "WIRE_RECALLED": "recall",
          "R01": "insufficient_funds", "R02": "account_closed", "R03": "no_account", "R04": "invalid_account",
          "R08": "stop_payment", "R10": "unauthorized", "R16": "account_frozen", "DAILY_LIMIT_EXCEEDED": "limit",
          "COMPLIANCE_HOLD": "review", "INVALID_ROUTING_NUMBER": "invalid_account",
          "BENEFICIARY_NAME_MISMATCH": "name_mismatch"}
PHRASES = {
    "insufficient_funds": r"\b(insufficient funds|not enough (money|funds|balance)|lack of funds)\b",
    "account_closed": r"\b(account (is|was|has been) closed|closed account)\b",
    "no_account": r"\b(no such account|no account matching|account (could not|couldn't|cannot) be (found|located)|"
                  r"account does not exist)\b",
    "invalid_account": r"\b(invalid|wrong|incorrect) (account|routing) number\b|"
                       r"\b(account|routing) number (is|was) (not valid|invalid|wrong|incorrect)\b",
    "stop_payment": r"\b(stop(ped)? (this |the )?payment|payment (was )?stopped|stop order)\b",
    "unauthorized": r"\b(not authori[sz]ed|unauthori[sz]ed)\b",
    "account_frozen": r"\baccount (is|was|has been) frozen\b",
    "limit": r"\b(exceed(s|ed)?|over|above|reached|hit) (the |your |a )?((daily|transfer|sending) )*limit\b",
    "name_mismatch": r"\bname (mismatch|did ?n[o']t match|does ?n[o']t match|does not match)\b",
    "rejected_by_bank": r"\breceiving bank rejected\b|\brejected by the receiving bank\b",
    "rail_unreachable": r"\b(cannot|can't|does not|doesn't) (receive|accept) (real-?time|instant) payments?\b",
    "irrevocable": r"\birrevocab\w+\b|\b(cannot|can't) be (cancelled|canceled|recalled|reversed)\b",
    "recall": r"\brecall\b",
}
_LAST4 = re.compile(r"^\d{4}$")


def _reason(d: dict) -> str:
    key = str(d.get("return_code") or d.get("error") or "")
    if not key and str(d.get("status", "")).lower() == "completed" and d.get("rail") in ("rtp", "wire"):
        return "SETTLED_IRREVOCABLE"       # the policy a cancel/recall question needs
    return key


def _summary(d: dict) -> str | None:
    status, amount = str(d.get("status", "")).lower(), money(d, "amount")
    rail, direction = ok(WORD, d.get("rail")), ok(WORD, d.get("direction"))
    if not (amount and rail and direction):
        return None
    head = f"Transfer {d['transfer_id']} ({rail}, {direction}) for {amount}"
    code, error = ok(KEY, d.get("return_code")), ok(KEY, d.get("error"))
    if status in TRANSFERS.open_statuses:
        line = f"{head} is {status}; its outcome is not confirmed yet."
        if error:
            line += f" It is held with status key {error}."
        if expected := ok(DATE, d.get("expected_on")):
            line += f" Expected on {expected}."
    elif status == "completed":
        done = ok(DATE, d.get("completed_on"))
        line = f"{head} was completed" + (f" on {done}." if done else ".")
    elif status == "returned" and code:
        line = f"{head} was returned with return code {code}."
    elif status == "failed" and error:
        line = f"{head} failed with error {error}."
    elif status == "cancelled":
        line = f"{head} was cancelled."
    else:
        return None
    if last4 := ok(_LAST4, d.get("to_account_last4")):
        line += f" The receiving account number ends in {last4}."
    return line


TRANSFERS = register(Domain(
    name="transfers", noun="transfer", prefix="xfer", tool="get_transfer", id_arg="transfer_id",
    source="transfer_db",
    description=("Fetch the authoritative record for one bank transfer belonging to the current customer: rail, "
                 "direction, status, amount, currency, return code or error, dates. Use whenever the user asks "
                 "about a specific transfer. Returns status=not_found if it does not exist for this customer."),
    fields=frozenset({"transfer_id", "rail", "direction", "status", "amount", "currency", "return_code", "error",
                      "created_at", "expected_on", "completed_on", "to_account_last4", "guidance"}),
    statuses={"completed": "completed", "settled": "completed", "pending": "pending",
              "processing": "processing", "returned": "returned", "failed": "failed", "rejected": "failed",
              "cancelled": "cancelled", "canceled": "cancelled"},
    open_statuses=frozenset({"pending", "processing"}), policy=POLICY,
    reason_key=_reason, summary=_summary,
    causes=CAUSES, cause_phrases=PHRASES))
