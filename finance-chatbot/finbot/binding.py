"""Claim binding: each stated value must come from the record its sentence is about.

The detail checks ask "does this amount, code or date appear in this turn's tool data?".
With one record that is enough. With two it is not: "txn_A for 12.66 was approved, txn_B
for 28.24 was declined" swaps two real values and passes every per-value check. Binding
ties each clause to one record and checks the clause against that record alone.

    segments()      split an answer into clauses and give each the record it is about: the
                    record id it names, or the one named just before it ("It was declined.")
    binding_check() amounts, dates and codes in a clause about record A must not come from
                    another record B; a payment status claimed for A must be A's status

A clause that names no record and follows none is left to the existing checks, which treat
it conservatively (domains/checks.py blocks a status any fetched record contradicts). Only
runs when two or more records were fetched; with one record nothing changes.
"""
from __future__ import annotations

import json
import re

from .domains import id_of, record_tools
from .provenance import trusted_view

# "the other one", "the second payment", "both": a reference the clause cannot resolve to
# one record, so it does not inherit the record named before it.
_AMBIGUOUS = re.compile(r"\b(other|another|second|first|former|latter|both|each|either|neither|"
                        r"one of|all of|the rest)\b", re.I)


def segments(answer: str, record_ids: set[str]) -> list[tuple[str, str | None]]:
    """(clause, record id or None) in answer order.

    A sentence naming one known record is about that record. A sentence naming several is cut
    at each id: the text from one id up to the next belongs to the earlier id, and text before
    the first id belongs to the first. A sentence naming none inherits the last record named,
    unless it refers to "the other one" or similar."""
    from .guardrails import _sentences
    out: list[tuple[str, str | None]] = []
    last: str | None = None
    for sentence in _sentences(answer):
        hits = [(m.start(), m.group(1)) for m in re.finditer(r"\b(\w+_[A-Za-z0-9]{6,})\b", sentence)
                if m.group(1) in record_ids]
        if not hits:
            subject = None if _AMBIGUOUS.search(sentence) else last
            out.append((sentence, subject))
            continue
        cuts = [0] + [pos for pos, _ in hits[1:]] + [len(sentence)]
        for i, (_, rid) in enumerate(hits):
            out.append((sentence[cuts[i]:cuts[i + 1]], rid))
        last = hits[-1][1]
    return out


# Two negative outcomes are still different facts: "declined" is the issuer's answer, "failed" a
# gateway or processing error, and they call for different next steps.
_PAYMENT_STATUS = {"declined": re.compile(r"\bdeclined\b", re.I), "failed": re.compile(r"\bfailed\b", re.I),
                   "approved": re.compile(r"\bapproved\b", re.I)}


def _other_status(clause: str, actual: str, others: set[str]) -> str | None:
    """A status word the clause affirms that is another fetched payment's status, not this one's."""
    from .guardrails import _affirmed
    for word, pattern in _PAYMENT_STATUS.items():
        if word != actual and word in others and _affirmed(pattern, clause):
            return word
    return None


def records(tool_log: list[dict]) -> dict[str, dict]:
    """Found records of this turn by id: {id: {"tool": name, "data": [their data and logs]}}."""
    out: dict[str, dict] = {}
    for c in tool_log:
        res = c["result"]
        rid = id_of(c["input"])
        if res["status"] != "found" or not rid:
            continue
        if c["name"] in record_tools():
            entry = out.setdefault(rid, {"tool": None, "data": []})
            entry["tool"] = c["name"]
            entry["data"].insert(0, res["data"])
        elif c["name"] == "search_transaction_logs":   # a payment's logs are evidence about that payment
            out.setdefault(rid, {"tool": None, "data": []})["data"].append(res["data"])
    return {k: v for k, v in out.items() if v["tool"]}


def _blob(values: list) -> str:
    return " ".join(json.dumps(trusted_view(v)) for v in values)


def _shared_blob(tool_log: list[dict]) -> str:
    """Evidence that belongs to no one record (help pages, processor health): a value found
    there may be stated in any clause."""
    return _blob([c["result"]["data"] for c in tool_log if c["result"]["status"] == "found"
                  and not id_of(c["input"])])


def binding_check(answer: str, tool_log: list[dict]) -> list[str]:
    from .domains.checks import _keys
    from .guardrails import AMOUNT, CODE, ISO_DATE, NEGATIVE, NEGATIVE_STATUSES, POSITIVE, UNKNOWN_STATUSES, _affirmed
    recs = records(tool_log)
    if len(recs) < 2:
        return []
    own = {rid: _blob(r["data"]) for rid, r in recs.items()}
    keys = {rid: {k for k in _keys(r["data"]) if re.search(r"[A-Za-z]", k) and len(k) >= 2}
            for rid, r in recs.items()}
    shared = _shared_blob(tool_log)
    pay = {rid: str(r["data"][0].get("status", "")).lower() for rid, r in recs.items()
           if r["tool"] == "get_transaction"}
    others = {rid: {st for o, st in pay.items() if o != rid} for rid in pay}
    reasons: list[str] = []

    def foreign(value: str, rid: str) -> str | None:
        """The other record a value comes from, if it isn't this record's (or shared)."""
        if value in own[rid] or value in shared:
            return None
        return next((o for o in recs if o != rid and value in own[o]), None)

    for clause, rid in segments(answer, set(recs)):
        if rid is None:
            continue
        values = [("amount", a) for a in AMOUNT.findall(clause)] + \
                 [("date", d) for d in ISO_DATE.findall(clause)] + \
                 [("code", c) for c in CODE.findall(clause)]
        for kind, v in values:
            if (other := foreign(v, rid)):
                reasons.append(f"value_from_other_record:{kind}:{rid}<-{other}")
        for o in recs:
            for k in keys[o] - keys[rid]:
                if re.search(rf"\b{re.escape(k)}\b", clause) and k not in own[rid] and k not in shared:
                    reasons.append(f"value_from_other_record:code:{rid}<-{o}")
        if recs[rid]["tool"] == "get_transaction":
            actual = str(recs[rid]["data"][0].get("status", "")).lower()
            if (actual in NEGATIVE_STATUSES and _affirmed(POSITIVE, clause)) or \
                    (actual == "approved" and _affirmed(NEGATIVE, clause)):
                reasons.append(f"status_mismatch:{rid}:record={actual}")
            elif (word := _other_status(clause, actual, others[rid])):
                reasons.append(f"status_mismatch:{rid}:record={actual},claimed={word}")
            elif actual in UNKNOWN_STATUSES and (_affirmed(POSITIVE, clause, hedges=True) or
                                                 _affirmed(NEGATIVE, clause, hedges=True)):
                reasons.append(f"definite_outcome_claimed:{rid}:record={actual}")
    return list(dict.fromkeys(reasons))
