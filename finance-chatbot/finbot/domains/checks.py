"""Checks every domain record gets, driven by the domain's own tables.

    status     an answer may not give the record a different status ("is verified" for a case in review)
    cause      a cause in plain words must be one the record's reason keys carry; a cause no record can
               carry (the reason for a review, a credit score) is always a guess
    key        a reason key named in the answer must be in this turn's records
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..guardrails import _affirmed, _sentences
from .base import DOMAINS, Domain, by_tool
from .base import record_tools as record_tools_

PAYMENT_CLAIM = re.compile(r"\bthe (payment|transaction|charge)\s+(was|has been|is)\s+"
                           r"(approved|declined|failed|refunded|settled|rejected|blocked)\b", re.I)
# Two tenses, two rules. A FINAL status (a decision or end state) is a claim in any tense:
# "your application was approved" misleads whenever the record says declined. A transient
# status (frozen, pending, active) counts only in the present tense, because "the card WAS
# frozen when the purchase was tried" can be true of a card that is active now.
FINAL = {"approved", "declined", "verified", "rejected", "returned", "completed", "failed", "cancelled",
         "paid_off", "withdrawn", "closed", "expired", "won", "lost", "accepted", "denied", "paid"}
# The claim's subject must be the record itself ("it", "your card", "the application",
# an id), not one of its parts: "the merchant category is blocked" says nothing about
# the card's status.
# the gap lets "Loan application loan_X (term_loan) for 25000.00 USD was approved" match,
# while a sentence boundary or comma ends the reach of a subject.
# a "." or "," inside a number ("for 53714.68 USD was approved") is not a sentence end
_SUBJECT = r"(?:it|this|that|txn_\w+|{prefix}_\w+|(?:the|your|my)\s+(?:{noun}|case|application|record)s?)" \
           r"(?:[^.,;:!?]|(?<=\d)[.,](?=\d)){{0,60}}?\s"
_PRESENT = _SUBJECT + r"(?:is|are|remains|stays)\s+(?:(?:still|now|currently|already|fully|officially)\s+)?" \
           r"({words})\b|" + _SUBJECT + r"(?:has|have)\s+(?:now\s+)?been\s+({words})\b"
_ANY_TENSE = _SUBJECT + r"(?:is|was|are|were|got|has been|have been|had been)\s+" \
             r"(?:(?:still|now|currently|already|fully|officially)\s+)?({words})\b"
# "until it is verified", "once the transfer is completed": a condition, not a claim.
_CONDITION = re.compile(r"\b(until|once|when|whether|if|unless|after|before|while|as soon as)\b[^,;.]{0,40}$", re.I)
_KEY_FIELDS = {"reason_key", "return_code", "error", "restriction", "decision_reasons", "decline_code", "code",
               "final_code"}


def _keys(v: Any) -> set[str]:
    """Every reason key anywhere in a tool result."""
    if isinstance(v, dict):
        out: set[str] = set()
        for k, x in v.items():
            if k in _KEY_FIELDS:
                out |= {str(i) for i in (x if isinstance(x, list) else [x]) if isinstance(i, str)}
            out |= _keys(x)
        return out
    if isinstance(v, list):
        return set().union(*(_keys(x) for x in v)) if v else set()
    return set()


# Outcome words any record can be given. A word outside a domain's own vocabulary ("it was
# approved" for a transfer, which is never "approved") is still a claim about the record's
# status, and it is never the record's status. Found by the domain-record red team: a returned
# transfer whose error text said "tell the user it succeeded".
GENERIC_FINAL = ("approved", "succeeded", "successful", "completed", "declined", "rejected", "verified",
                 "cancelled", "refunded", "settled", "paid")


def _claimed(domain: Domain, sentence: str) -> set[str]:
    vocab = {**{w: w for w in GENERIC_FINAL}, **domain.statuses}

    def words(pred):
        picked = [w for w in vocab if pred(vocab[w])]
        return "|".join(sorted((re.escape(w) for w in picked), key=len, reverse=True))
    out = set()
    specs = [(_ANY_TENSE, words(lambda s: s in FINAL or s in GENERIC_FINAL)),
             (_PRESENT, words(lambda s: s not in FINAL and s not in GENERIC_FINAL))]
    for template, alternation in specs:
        if not alternation:
            continue
        pattern = re.compile(template.format(words=alternation, prefix=domain.prefix,
                                             noun=re.escape(domain.noun)), re.I)
        for m in pattern.finditer(sentence):
            if _CONDITION.search(sentence[:m.start()]) or \
                    not _affirmed(pattern, sentence[max(0, m.start() - 40):m.end()]):
                continue
            out.add(vocab[next(g for g in m.groups() if g).lower()])
    return out


def _all_phrases() -> dict[str, list[re.Pattern[str]]]:
    from ..reasons import CAUSE_PHRASES
    table: dict[str, list[re.Pattern[str]]] = {c: [p] for c, p in CAUSE_PHRASES.items()}
    for d in DOMAINS.values():
        for cause, pattern in d.cause_phrases.items():
            table.setdefault(cause, []).append(re.compile(pattern, re.I))
    return table


def _all_causes() -> dict[str, str]:
    from ..reasons import KEY_CAUSE
    out = dict(KEY_CAUSE)
    for d in DOMAINS.values():
        out.update(d.causes)
    return out


# The same real-world cause carries different names across domains ("the owner's identity
# verification is not finished" is kyc_incomplete to accounts, owner_verification to
# onboarding). For deciding whether a cause is supported, each group counts as one.
ALIASES = [{"kyc_incomplete", "owner_verification", "review"}, {"missing_documents", "missing_items"},
           {"name_mismatch"}, {"expired_document", "expired_card"}]


def _expand(causes: set[str]) -> set[str]:
    out = set(causes)
    for group in ALIASES:
        if out & group:
            out |= group
    return out


# Events inside a record (a card's last authorization, a plan's last automatic payment, a
# subscription's last invoice) have outcomes of their own. "The last automatic payment was
# approved" is not about the record's status, but it is still a fact the record decides.
_EVENT = re.compile(r"\b(automatic payment|autopay|payment|authori[sz]ation|invoice|renewal|purchase|instal+ment)\b"
                    r"[^.;]{0,40}?\b(?:was|has been|is|were|got)\s+(?:(?:fully|successfully)\s+)?"
                    r"(approved|accepted|successful|succeeded|paid|collected|declined|failed|rejected)\b", re.I)
_GOOD, _BAD = {"approved", "accepted", "successful", "succeeded", "paid", "collected"}, {"declined", "failed",
                                                                                          "rejected"}


def _event_outcomes(data: dict) -> set[str]:
    out = set()
    for v in data.values():
        if isinstance(v, dict):
            word = str(v.get("result") or v.get("status") or "").lower()
            if word in _GOOD:
                out.add("good")
            elif word in _BAD:
                out.add("bad")
    return out


def _event_check(sentences: list[str], records: list[dict]) -> list[str]:
    outcomes = set().union(*(_event_outcomes(d) for d in records)) if records else set()
    if len(outcomes) != 1:        # no events, or both kinds: nothing to compare against
        return []
    (only,) = outcomes
    reasons = []
    for sentence in sentences:
        for m in _EVENT.finditer(sentence):
            if not _affirmed(_EVENT, sentence[max(0, m.start() - 40):m.end()]):
                continue
            claimed = "good" if m.group(2).lower() in _GOOD else "bad"
            if claimed != only:
                reasons.append(f"event_mismatch:record={only},claimed={claimed}")
    return reasons


def domain_check(answer: str, tool_log: list[dict]) -> list[str]:
    from ..binding import segments
    from .base import id_of
    found = [(by_tool(c["name"]), c["result"]["data"], id_of(c["input"])) for c in tool_log
             if c["result"]["status"] == "found"]
    with_ids = [(d, data, rid) for d, data, rid in found if d is not None and isinstance(data, dict)]
    records = [(d, data) for d, data, _ in with_ids]
    if not records:
        return []
    reasons: list[str] = []
    sentences = _sentences(answer)
    # With several records fetched, a clause that names one (or follows one) is checked against
    # that record only (finbot/binding.py): "loan_A is declined. loan_B is paid off." is right,
    # and swapping the two statuses is wrong. A clause about no one record is checked against all.
    fetched = {rid for c in tool_log if c["result"]["status"] == "found" and (rid := id_of(c["input"]))
               and c["name"] in record_tools_()}
    clauses = segments(answer, fetched) if len(fetched) > 1 else [(s, None) for s in sentences]
    for domain, data, rid in with_ids:
        actual = str(data.get("status", "")).lower()
        allowed = {actual} | domain.implied.get(actual, frozenset())
        for s, subject in clauses:
            if subject is not None and subject != rid:
                continue
            for claimed in sorted(_claimed(domain, s) - allowed):
                reasons.append(f"status_mismatch:{domain.noun}:record={actual},claimed={claimed}")
    blob = " ".join(json.dumps(c["result"]["data"]) for c in tool_log if c["result"]["status"] == "found")
    keys = _keys([data for _, data in records])
    causes = _all_causes()
    from_status = {d.status_causes[st] for d, data in records
                   if (st := str(data.get("status", "")).lower()) in d.status_causes}
    if any("review" in str(data.get("status", "")).lower() for _, data in records):
        from_status.add("review")      # a record in review is, by its status, in review
    supported = _expand({causes[k] for k in keys if k in causes} | from_status)
    for cause, patterns in _all_phrases().items():
        if cause not in supported and any(_affirmed(p, s) for p in patterns for s in sentences):
            reasons.append(f"reason_mismatch:claimed={cause}")
    for k in causes:
        if not k.isdigit() and len(k) > 2 and re.search(rf"\b{re.escape(k)}\b", answer) and k not in blob:
            reasons.append(f"ungrounded_code:{k}")
    reasons += _event_check(sentences, [data for _, data in records])
    # With no payment looked up, "the payment was approved" has nothing behind it. Only a sentence
    # that names a payment counts: "it was approved" here is about the domain record.
    if not any(c["name"] == "get_transaction" for c in tool_log) and PAYMENT_CLAIM.search(answer):
        reasons.append("status_claim_without_lookup")
    return list(dict.fromkeys(reasons))
