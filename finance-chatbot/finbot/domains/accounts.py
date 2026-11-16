"""Bank accounts: balance, available balance, holds, fees, restrictions.

The rules are an EXAMPLE policy that matches the synthetic help pages. Replace them with
your own account terms before production.
"""
from __future__ import annotations

from .base import DATE, KEY, WORD, Domain, Rule, money, ok, register

_HOLD = "The held amount becomes available on the release date shown. A hold can't be lifted in chat."
_HOLD_STAFF = "Quote the release date on the record. Releasing a hold early needs the operations team."

POLICY = {
    "KYC_INCOMPLETE": Rule("Identity verification for this account is not finished.",
                           "Finish identity verification to lift the restriction.",
                           "Restricted until identity verification is finished; check the verification case."),
    "UNDER_REVIEW": Rule("The account is under review.",
                         "No action is needed now. We can't share details of a review; we'll contact you if "
                         "we need anything.",
                         "Under review. Do not disclose or guess the reason; pass questions to the compliance team."),
    "DORMANT": Rule("The account is dormant because it has had no activity for a long period.",
                    "Contact support to confirm your identity and reopen it for use.",
                    "Dormant. Reopening needs an identity check by a person."),
    "OVERDRAWN": Rule("The balance is below zero.",
                      "Bring the balance back above zero. Overdraft fees follow the fee schedule.",
                      "Overdrawn. Fees follow the fee schedule; do not waive fees in chat."),
    "CLOSED_BY_CUSTOMER": Rule("The account was closed at the account holder's request.",
                               "A closed account can't be reopened in chat. Contact support to open a new one.",
                               "Closed at the holder's request. A new account needs a new application."),
    "CHECK_DEPOSIT": Rule("Funds from a deposited check are held until the check clears.", _HOLD, _HOLD_STAFF),
    "CARD_AUTHORIZATION": Rule("A card authorization is reserving these funds until the purchase posts.",
                               _HOLD, _HOLD_STAFF),
    "LARGE_DEPOSIT_REVIEW": Rule("A large deposit is held while it is checked.", _HOLD, _HOLD_STAFF),
}

CAUSES = {"KYC_INCOMPLETE": "kyc_incomplete", "UNDER_REVIEW": "review", "DORMANT": "dormant",
          "OVERDRAWN": "overdrawn", "CLOSED_BY_CUSTOMER": "closed_by_customer", "CHECK_DEPOSIT": "check_hold",
          "CARD_AUTHORIZATION": "card_auth", "LARGE_DEPOSIT_REVIEW": "deposit_review"}
PHRASES = {
    "kyc_incomplete": r"\b(identity )?verification (is |was )?(incomplete|not complete|not finished|unfinished)\b",
    "review": r"\b(under|compliance|security) review\b|\bbeing reviewed\b",
    "dormant": r"\bdormant\b|\bno activity\b",
    "overdrawn": r"\boverdrawn\b|\bnegative balance\b|\bbelow zero\b",
    "check_hold": r"\bche(ck|que) (deposit|hold|clearing|clears)\b|\bdeposited che(ck|que)\b",
    "card_auth": r"\bcard authori[sz]ation\b|\bpre-?authori[sz]ation\b",
    "deposit_review": r"\blarge deposit\b",
    # Never on a record the model can see, so stating one is always a guess.
    "legal_or_sanctions": r"\b(court order|garnish\w+|levy|sanction\w*|watch ?list|money laundering)\b",
}


def _reason(d: dict) -> str:
    if str(d.get("status", "")).lower() != "active" and d.get("restriction"):
        return str(d["restriction"])
    holds = d.get("holds") or []
    return str(holds[0].get("reason_key", "")) if holds and isinstance(holds[0], dict) else ""


def _summary(d: dict) -> str | None:
    status, kind = str(d.get("status", "")).lower(), ok(WORD, d.get("type"))
    balance, available = money(d, "balance"), money(d, "available_balance")
    if status not in ACCOUNTS.statuses.values() or not (kind and balance and available):
        return None
    line = f"Account {d['account_id']} ({kind}) is {status}. Balance {balance}; available {available}."
    for h in d.get("holds") or []:
        amount, key, date = money({**h, "currency": d["currency"]}, "amount"), ok(KEY, h.get("reason_key")), \
            ok(DATE, h.get("release_date"))
        if amount and key and date:
            line += f" Hold of {amount} ({key}), release date {date}."
    fees = d.get("fees") or []
    if fees:
        f = fees[-1]
        amount, kind, date = money({**f, "currency": d["currency"]}, "amount"), ok(WORD, f.get("type")), \
            ok(DATE, f.get("posted_on"))
        if amount and kind and date:
            line += f" Latest fee: {kind} {amount}, posted {date}."
    return line


ACCOUNTS = register(Domain(
    name="accounts", noun="account", prefix="acct", tool="get_account", id_arg="account_id", source="account_db",
    description=("Fetch the authoritative record for one bank account belonging to the current customer: type, "
                 "status, balance, available balance, holds with their release dates, recent fees. Use whenever "
                 "the user asks about a specific account, a balance, a hold or a fee. Returns status=not_found "
                 "if it does not exist for this customer."),
    fields=frozenset({"account_id", "type", "status", "currency", "balance", "available_balance", "holds", "fees",
                      "opened_on", "restriction", "guidance"}),
    statuses={"active": "active", "frozen": "frozen", "closed": "closed", "restricted": "restricted"},
    open_statuses=frozenset(), policy=POLICY, reason_key=_reason, summary=_summary,
    causes=CAUSES, cause_phrases=PHRASES))
