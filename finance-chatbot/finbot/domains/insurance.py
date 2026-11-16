"""Insurance claims: status, amounts claimed, approved and paid, the deductible, denial reasons.

The bot reports the claim record and the recorded denial reason. It never interprets the
policy wording ("you're covered" is a coverage opinion, not a record fact) and never sees
the adjuster's notes or any fraud score, so it can't repeat or hint at them.

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

from .base import DATE, KEY, WORD, Domain, Rule, money, ok, register

_APPEAL = (" You can appeal within 60 days of the decision. An appeal is reviewed by a person; its outcome can't "
           "be predicted in chat.")
_STAFF = " An appeal is a ticket for the claims team. Do not predict the outcome or interpret the policy wording."

POLICY = {
    "NOT_COVERED": Rule("The loss is not a type the policy covers.",
                        "The decision letter quotes the policy section." + _APPEAL,
                        "Denied: not a covered peril." + _STAFF),
    "EXCLUSION_APPLIES": Rule("An exclusion in the policy applies to this loss.",
                              "The decision letter names the exclusion." + _APPEAL, "Denied: exclusion." + _STAFF),
    "POLICY_LAPSED": Rule("The policy was not in force on the date of loss.",
                          "The decision letter gives the lapse date." + _APPEAL, "Denied: lapsed policy." + _STAFF),
    "WAITING_PERIOD": Rule("The loss happened inside the policy's waiting period.",
                           "The decision letter gives the waiting period's end date." + _APPEAL,
                           "Denied: waiting period." + _STAFF),
    "DOCS_INCOMPLETE": Rule("The claim is waiting for the documents listed.",
                            "Upload the documents listed to continue the claim.",
                            "Waiting on the customer's documents."),
    "CLAIM_IN_REVIEW": Rule("An adjuster is reviewing the claim.",
                            "No action is needed now. The decision can't be predicted in chat.",
                            "With an adjuster. Do not predict the outcome or the amount."),
}

CAUSES = {"NOT_COVERED": "not_covered", "EXCLUSION_APPLIES": "exclusion", "POLICY_LAPSED": "lapsed",
          "WAITING_PERIOD": "waiting_period", "DOCS_INCOMPLETE": "missing_documents", "CLAIM_IN_REVIEW": "review"}
PHRASES = {
    "not_covered": r"\bnot (a )?covered\b|\bnot covered by\b",
    "exclusion": r"\bexclusion\b",
    "lapsed": r"\blapsed\b|\bnot in force\b",
    "waiting_period": r"\bwaiting period\b",
    # A coverage opinion: the policy wording decides it, not the chat. Never a record fact.
    "coverage_opinion": r"\b(you are|you're|this is|it is|it's|should be|would be|will be) (fully |probably |likely |"
                        r"definitely )?covered\b",
}


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    if status == "denied":
        return str(d.get("denial_reason") or "")
    return {"needs_documents": "DOCS_INCOMPLETE", "in_review": "CLAIM_IN_REVIEW"}.get(status, "")


def _summary(d: dict) -> str | None:
    status, kind, claimed = str(d.get("status", "")).lower(), ok(WORD, d.get("policy_type")), money(d, "claimed_amount")
    if not (kind and claimed):
        return None
    head = f"Claim {d['claim_id']} ({kind}) for {claimed}"
    if status == "in_review":
        return f"{head} is in review; no decision has been made yet."
    if status == "submitted":
        return f"{head} is submitted; review has not started."
    if status == "needs_documents":
        docs = [x for x in d.get("documents_required") or [] if ok(WORD, x)]
        return f"{head} needs documents: {', '.join(docs)}." if docs else None
    if status == "denied" and (key := ok(KEY, d.get("denial_reason"))):
        done = ok(DATE, d.get("decided_on"))
        return f"{head} was denied" + (f" on {done}" if done else "") + f" with reason {key}."
    approved = money(d, "approved_amount")
    if status in ("approved", "paid") and approved:
        line = f"{head} was approved for {approved}"
        if deductible := money(d, "deductible"):
            line += f" after a deductible of {deductible}"
        line += "."
        if status == "paid" and (when := ok(DATE, d.get("paid_on"))):
            line += f" It was paid on {when}."
        return line
    return None


INSURANCE = register(Domain(
    name="insurance", noun="claim", prefix="clm", tool="get_claim", id_arg="claim_id", source="claims_db",
    description=("Fetch the authoritative record for one insurance claim belonging to the current customer: policy "
                 "type, status, amounts claimed, approved and paid, the deductible, the recorded denial reason and "
                 "documents still required. Use whenever the user asks about a specific claim. Returns "
                 "status=not_found if it does not exist for this customer."),
    fields=frozenset({"claim_id", "policy_type", "status", "currency", "claimed_amount", "approved_amount",
                      "deductible", "denial_reason", "documents_required", "submitted_on", "decided_on", "paid_on",
                      "guidance"}),
    statuses={"approved": "approved", "denied": "denied", "rejected": "denied", "paid": "paid",
              "in review": "in_review", "under review": "in_review", "submitted": "submitted"},
    open_statuses=frozenset({"submitted", "in_review", "needs_documents"}), policy=POLICY, reason_key=_reason,
    summary=_summary, causes=CAUSES, cause_phrases=PHRASES,
    implied={"paid": frozenset({"approved"})}))
