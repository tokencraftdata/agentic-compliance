"""Identity verification (KYC) cases: status, what is missing, what may be submitted again.

Two rules matter more than wording. A case that is not decided is never described as
verified or rejected. And the reason a case is under review is never given to the model
(the field is not in `fields`), so it cannot be repeated or hinted at.

The rules are an EXAMPLE policy that matches the synthetic help pages.
"""
from __future__ import annotations

from .base import DATE, KEY, WORD, Domain, Rule, ok, register

POLICY = {
    "DOC_EXPIRED": Rule("The identity document had passed its expiry date.",
                        "Upload a document that is still valid.",
                        "Rejected: document out of date. The customer can submit a valid one.",
                        True, "You can resubmit with a document that is still valid."),
    "DOC_UNREADABLE": Rule("The photo of the document could not be read.",
                           "Upload a clear, uncropped photo with all four corners visible.",
                           "Rejected: image quality. The customer can submit a clearer photo.",
                           True, "You can resubmit with a clear, uncropped photo."),
    "NAME_MISMATCH": Rule("The name on the document does not match the name on the profile.",
                          "Upload a document in the profile's name, or ask support to correct the name.",
                          "Rejected by the name check. A name correction needs a ticket.",
                          True, "You can resubmit with a document in the same name as the profile."),
    "ADDRESS_UNVERIFIED": Rule("The proof of address could not be accepted.",
                               "Upload a proof of address dated within the last 3 months.",
                               "Rejected: proof of address. The customer can submit a recent one.",
                               True, "You can resubmit with a proof of address dated within the last 3 months."),
    "SELFIE_MISMATCH": Rule("The selfie did not match the photo on the document.",
                            "Take a new selfie in good light, without glasses or a hat.",
                            "Rejected by the face check. After a second failure a person reviews the case.",
                            True, "You can resubmit once; after that a person reviews the case."),
    "DOCUMENTS_MISSING": Rule("The case is waiting for the documents listed.",
                              "Upload the documents listed to continue.",
                              "Waiting for the customer's documents."),
    "UNDER_REVIEW": Rule("The case is with a reviewer and no decision has been made.",
                         "No action is needed now. We can't share details of a review; we'll contact you if we "
                         "need anything.",
                         "With a reviewer. Do not disclose or guess the reason and do not predict the outcome; "
                         "pass questions to the compliance team.",
                         False, "Do not resubmit while the case is with a reviewer."),
    "DUPLICATE_PROFILE": Rule("Another profile with the same identity already exists.",
                              "Do not open another profile. Contact support so the profiles can be merged.",
                              "Duplicate profile. Merging needs a person; do not ask the customer to register again.",
                              False, "Do not resubmit and do not open another profile."),
}

CAUSES = {"DOC_EXPIRED": "expired_document", "DOC_UNREADABLE": "unreadable", "NAME_MISMATCH": "name_mismatch",
          "ADDRESS_UNVERIFIED": "address_proof", "SELFIE_MISMATCH": "selfie", "DOCUMENTS_MISSING": "missing_documents",
          "UNDER_REVIEW": "review", "DUPLICATE_PROFILE": "duplicate"}
_DOC = r"(document|id|id card|passport|licen[cs]e|permit)"
PHRASES = {
    "expired_document": rf"\b{_DOC} (has |had |is |was )?(expired|out of date)\b|\bexpired {_DOC}\b|"
                        r"\bpassed its expiry date\b",
    "unreadable": r"\b(unreadable|blurry|blurred|too dark|not legible|illegible|could not be read)\b",
    "name_mismatch": r"\bname (mismatch|did ?n[o']t match|does ?n[o']t match|does not match)\b",
    "address_proof": r"\b(proof of address|address (could not|couldn't) be (verified|confirmed))\b",
    "selfie": r"\b(selfie|face) (did ?n[o']t|does ?n[o']t|did not|does not) match\b|\b(selfie|face) mismatch\b",
    "duplicate": r"\bduplicate (profile|account|identity)\b|\banother profile with the same identity\b",
    "review": r"\b(under|compliance|security) review\b|\bbeing reviewed\b|\bwith a reviewer\b",
    # The reason for a review is never in a record the model can see: stating one is a guess.
    "screening_hit": r"\b(sanction\w*|watch ?list|politically exposed|money laundering|adverse media|"
                     r"fraud (alert|flag|risk))\b",
}


def _reason(d: dict) -> str:
    status = str(d.get("status", "")).lower()
    if status == "in_review":
        return "UNDER_REVIEW"
    if status == "needs_documents":
        return "DOCUMENTS_MISSING"
    return str(d.get("reason_key") or "") if status == "rejected" else ""


def _summary(d: dict) -> str | None:
    status, level = str(d.get("status", "")).lower(), ok(WORD, d.get("level"))
    if not level:
        return None
    head = f"Verification case {d['case_id']} ({level})"
    if status == "verified":
        done = ok(DATE, d.get("decided_on"))
        return f"{head} is verified" + (f"; decided on {done}." if done else ".")
    if status == "rejected" and (key := ok(KEY, d.get("reason_key"))):
        return f"{head} was rejected with reason {key}."
    if status == "in_review":
        return f"{head} is in review; no decision has been made yet."
    if status == "pending":
        return f"{head} is pending; no decision has been made yet."
    if status == "needs_documents":
        docs = [x for x in d.get("documents_required") or [] if ok(WORD, x)]
        return f"{head} needs documents: {', '.join(docs)}." if docs else None
    return None


KYC = register(Domain(
    name="kyc", noun="verification case", prefix="kyc", tool="get_verification", id_arg="case_id", source="kyc_db",
    description=("Fetch the authoritative record for one identity verification (KYC) case belonging to the "
                 "current customer: status, level, the reason for a rejection, documents still required, dates. "
                 "Use whenever the user asks about a specific verification case. Returns status=not_found if it "
                 "does not exist for this customer."),
    fields=frozenset({"case_id", "status", "level", "reason_key", "documents_required", "submitted_on",
                      "decided_on", "guidance"}),
    statuses={"verified": "verified", "rejected": "rejected", "in review": "in_review",
              "under review": "in_review", "pending": "pending"},
    open_statuses=frozenset({"pending", "in_review", "needs_documents"}), policy=POLICY,
    reason_key=_reason, summary=_summary, causes=CAUSES, cause_phrases=PHRASES))
