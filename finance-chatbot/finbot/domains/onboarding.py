"""Onboarding applications: the stage, what is missing, recorded decision reasons.

The bot reports the stage and the listed missing items, never predicts the decision or a
decision date, and never gives a reason the record doesn't carry. The reason an
application is in review is not in `fields`, so the model never sees it.

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

from .base import DATE, KEY, WORD, Domain, Rule, ok, register

_LETTER = "The recorded reasons are in the decision letter."

POLICY = {
    "OB01": Rule("The business type is not supported.", _LETTER + " This decision is final for this business type.",
                 "Declined: unsupported business type. Do not suggest reapplying as the same business."),
    "OB02": Rule("The business registration could not be verified.",
                 "Check the registration details and upload the registration document, then apply again.",
                 "Declined: registration not verified. A corrected application is allowed.",
                 True, "You can apply again with the corrected registration details."),
    "OB03": Rule("Owner identity verification was not completed.",
                 "Each listed owner must finish identity verification, then apply again.",
                 "Declined: owner verification incomplete. A new application after verification is allowed.",
                 True, "You can apply again once every listed owner has finished verification."),
    "OB04": Rule("The country of operation is not supported.", _LETTER + " This decision is final for this country.",
                 "Declined: unsupported country. Do not suggest reapplying from the same country."),
    "DRAFT": Rule("The application has not been submitted.",
                  "Finish the remaining steps in the app and submit it.",
                  "Not submitted yet; the customer still has it in draft."),
    "NEEDS_INFORMATION": Rule("The application is waiting for the items listed.",
                              "Complete the missing items listed in the app to continue.",
                              "Waiting on the customer's documents or details."),
    "IN_REVIEW": Rule("The application is being reviewed.",
                      "No action is needed now. A decision date can't be promised in chat.",
                      "In review. Do not predict the outcome or a date; pass questions to the onboarding team."),
}

CAUSES = {"OB01": "business_type", "OB02": "registration", "OB03": "owner_verification", "OB04": "country",
          "DRAFT": "not_submitted", "NEEDS_INFORMATION": "missing_items", "IN_REVIEW": "review"}
PHRASES = {
    "business_type": r"\bbusiness type\b[^.]{0,30}\b(not supported|unsupported)\b|"
                     r"\b(not supported|unsupported) business type\b",
    "registration": r"\bregistration\b[^.]{0,40}\b(could not|couldn't|was not|wasn't|not) (be )?verified\b",
    "owner_verification": r"\bowner\b[^.]{0,40}\bverification\b|\bowners? (was|were|have|has) not (been )?verified\b",
    "country": r"\bcountry\b[^.]{0,40}\b(not supported|unsupported)\b",
    "not_submitted": r"\bnot (been )?submitted\b|\bstill in draft\b",
    "missing_items": r"\bmissing (items|documents|information)\b|\bwaiting (for|on) (your|the customer)\b",
    "review": r"\b(under|in|compliance|security) review\b|\bbeing reviewed\b",
}


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    reasons = d.get("decision_reasons") or []
    if status == "declined" and reasons:
        return str(reasons[0])
    return {"draft": "DRAFT", "needs_information": "NEEDS_INFORMATION", "in_review": "IN_REVIEW"}.get(status, "")


def _summary(d: dict) -> str | None:
    status, product = str(d.get("status", "")).lower(), ok(WORD, d.get("product"))
    if not product:
        return None
    head = f"Application {d['application_id']} ({product})"
    if status == "approved":
        done = ok(DATE, d.get("decided_on"))
        return f"{head} was approved" + (f" on {done}." if done else ".")
    if status == "declined":
        keys = [k for k in d.get("decision_reasons") or [] if ok(KEY, k)]
        if not keys:
            return None
        done = ok(DATE, d.get("decided_on"))
        return f"{head} was declined" + (f" on {done}" if done else "") + f". Reasons on record: {', '.join(keys)}."
    if status == "needs_information":
        items = [x for x in d.get("missing_items") or [] if ok(WORD, x)]
        return f"{head} needs information: {', '.join(items)}." if items else None
    if status in ("draft", "submitted", "in_review"):
        tail = {"draft": "is in draft; it has not been submitted", "submitted": "is submitted; review has not started",
                "in_review": "is in review; no decision has been made yet"}[status]
        return f"{head} {tail}."
    if status == "withdrawn":
        return f"{head} was withdrawn."
    return None


ONBOARDING = register(Domain(
    name="onboarding", noun="application", prefix="app", tool="get_application", id_arg="application_id",
    source="onboarding_db",
    description=("Fetch the authoritative record for one onboarding application belonging to the current "
                 "customer: product, stage, missing items, recorded decision reasons, dates. Use whenever the "
                 "user asks about an application's progress or outcome. Returns status=not_found if it does "
                 "not exist for this customer."),
    fields=frozenset({"application_id", "product", "status", "missing_items", "decision_reasons", "submitted_on",
                      "decided_on", "guidance"}),
    statuses={"approved": "approved", "declined": "declined", "denied": "declined", "rejected": "declined",
              "in review": "in_review", "under review": "in_review", "submitted": "submitted", "draft": "draft",
              "withdrawn": "withdrawn"},
    open_statuses=frozenset({"draft", "submitted", "in_review", "needs_information"}), policy=POLICY,
    reason_key=_reason, summary=_summary, causes=CAUSES, cause_phrases=PHRASES))
