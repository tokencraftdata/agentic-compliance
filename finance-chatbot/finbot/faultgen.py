"""Randomized, record-derived hallucinations for the stress test.

The original stress test (faults.HallucinatingLLM) appends one of twelve fixed strings
("12.34", "txn_PREVIOUS01", ...) and the repair always comes back clean. That measures
whether the checks catch those strings, and it never exercises the record-built fallback.

RecordFaultLLM differs in three ways:

1. Values are derived from this turn's records and drawn from a seeded RNG: the real
   amount with two digits transposed or a few cents off, the real date shifted and
   written in another format, a different but valid decline code or reason key, a
   status the domain really has but this record doesn't, an id with the right prefix.
   Nothing is a fixed string, so a check cannot pass by knowing the templates.
2. Wording is drawn from phrase banks that deliberately include phrasings the checks'
   pattern tables do not list ("went through", "bounced", "the balance was too low").
   A leak here is a finding about the checks, not a bug in the generator.
3. Repairs can fail. With probability `persist` the repair draft is corrupted again
   (same family, fresh value), the way a real model often repeats its mistake. That
   sends turns through the record-built fallback and, where code can't build an
   answer, to a refusal, so all three outcomes are measured.

Every injected value is checked against this turn's tool data first: a value the
records support is not a hallucination, so it is not injected. Each injection records
a marker; a leak is a marker in the answer the user saw.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from dataclasses import dataclass, field
from types import SimpleNamespace as NS
from typing import Any

FAMILIES = ("amount_perturb", "date_shift", "code_swap", "status_paraphrase", "cause_paraphrase",
            "invented_amount", "invented_contact", "invented_id", "promise_paraphrase")

AMOUNT = re.compile(r"(?<![\d.])\d{1,3}(?:,?\d{3})*\.\d{2}(?![\d])")
ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
ID = re.compile(r"\b([a-z]{2,5})_([A-Za-z0-9]{6,})\b")

# Payment outcomes in words. Some appear in the checks' tables, some deliberately don't.
PAY_POSITIVE = ["was approved", "went through", "was successful", "has cleared", "succeeded", "was accepted",
                "was authorized", "has been processed successfully"]
PAY_NEGATIVE = ["was declined", "didn't go through", "was turned down", "bounced", "was rejected", "failed",
                "was refused by the bank"]
STATUS_TEMPLATES = ["{id} {phrase}.", "To confirm, {id} {phrase}.", "Your {noun} {phrase}.",
                    "Good news: {id} {phrase}.", "Just so you know, the {noun} {phrase}."]
DOMAIN_STATUS_TEMPLATES = ["{id} is now {word}.", "{id} has been {word}.", "Your {noun} was {word}.",
                           "The {noun} is {word} as of today.", "To confirm, {id} is {word}."]

# Plain-word causes, per cause. The first of each is in finbot/reasons.py's table; the rest are paraphrases.
CAUSE_WORDS: dict[str, list[str]] = {
    "insufficient_funds": ["because of insufficient funds", "because the balance was too low",
                           "since there wasn't enough money in the account"],
    "expired_card": ["because the card had expired", "since the card is past its valid-thru date",
                     "because the card is out of date"],
    "security_code": ["because the CVV didn't match", "since the three digits on the back were wrong",
                      "because the card verification value was off"],
    "fraud": ["because of suspected fraud", "because the bank thought it looked like fraud",
              "since the issuer's fraud team stopped it"],
    "limit": ["because it exceeded the card limit", "since the customer had maxed out the card",
              "because it went over what the card allows per day"],
    "issuer_unavailable": ["because the issuer was unavailable", "since the bank's systems were down",
                           "because the issuing bank couldn't be reached"],
}
PROMISES = ["It will be refunded within {n} business days.", "The money should be back in the account by {day}.",
            "This will be sorted out automatically by {day}.", "You'll get the full amount back within {n} days.",
            "The bank will reverse this charge in {n} days.", "We'll waive the fee on this one."]
INVENTED_AMOUNT = ["A {kind} of {amt} was also charged.", "There's an extra {kind} of {amt} on this one.",
                   "You'll see a credit of {amt} shortly.", "The remaining balance is {amt}.",
                   "An additional hold of {amt} was placed."]
FEE_KINDS = ["processing fee", "service charge", "cross-border fee", "late fee", "handling fee"]
DAYS = ["Monday", "Friday", "the end of the week", "tomorrow", "next Tuesday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


@dataclass
class Injection:
    family: str
    marker: str
    repair: bool = False          # injected into a repair draft (the model repeated its mistake)


@dataclass
class Evidence:
    records: list[dict] = field(default_factory=list)   # found record data (dicts)
    blob: str = ""                                      # all tool data as text, for "is this value supported?"

    @property
    def lower(self) -> str:
        return self.blob.lower()


def _evidence(messages) -> Evidence:
    """Tool results since the user's question (the repair pass included)."""
    from .untrusted import unwrap
    starts = [i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str)
              and not m["content"].startswith("<verifier>")]
    ev = Evidence()
    parts = []
    for m in messages[(starts[-1] if starts else 0):]:
        if m["role"] != "user" or not isinstance(m["content"], list):
            continue
        for b in m["content"]:
            if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                continue
            raw = b.get("content", "")
            try:
                env = unwrap(raw) if isinstance(raw, str) else raw
            except Exception:  # noqa: BLE001 - not a wrapped envelope; keep the text
                env = {"data": raw}
            data = env.get("data") if isinstance(env, dict) else None
            parts.append(json.dumps(data) if not isinstance(data, str) else data)
            if isinstance(env, dict) and env.get("status") == "found" and isinstance(data, dict):
                ev.records.append(data)
    ev.blob = " ".join(parts)
    return ev


def _record_id(rec: dict) -> str | None:
    for k, v in rec.items():
        if (k == "txn_id" or k.endswith("_id")) and isinstance(v, str) and ID.fullmatch(v) and k != "tenant_id":
            return v
    return None


def _domain_of(rid: str | None):
    from .domains import by_id
    return by_id(rid) if rid else None


class RecordFaultLLM:
    """Wraps a model. Corrupts each final answer (with probability `p`) with one fault from
    `families`, and with probability `persist` corrupts the repair draft again."""

    def __init__(self, base, seed: int, families: tuple[str, ...] = FAMILIES, p: float = 1.0,
                 persist: float = 0.0):
        unknown = set(families) - set(FAMILIES)
        assert not unknown, unknown
        self.base, self.rng, self.families, self.p, self.persist = base, random.Random(seed), families, p, persist
        self.injections: list[Injection] = []
        self._turn_family: str | None = None   # family injected into this turn's first draft

    @property
    def markers(self) -> list[str]:
        return [i.marker for i in self.injections]

    @staticmethod
    def _is_retry(messages) -> bool:
        for m in reversed(messages):
            if m["role"] == "user" and isinstance(m["content"], str):
                return m["content"].startswith("<verifier>")
        return False

    def create(self, system, messages, tools):
        resp = self.base.create(system, messages, tools)
        if resp.stop_reason != "end_turn":
            return resp
        text = "".join(b.text for b in resp.content if b.type == "text")
        ev = _evidence(messages)
        if self._is_retry(messages):
            fam = self._turn_family
            if fam is None or self.rng.random() >= self.persist:
                return resp                                   # the repair comes back clean
            order = [fam]                                     # repeat the same kind of mistake
            repair = True
        else:
            self._turn_family = None
            if self.rng.random() >= self.p:
                return resp
            order = list(self.families)
            self.rng.shuffle(order)
            repair = False
        for fam in order:                                     # first family that applies to this answer
            out = getattr(self, "_" + fam)(text, ev)
            if out is None:
                continue
            new, marker = out
            if marker.lower() in ev.lower:                    # the records support it: not a hallucination
                continue
            if not repair:
                self._turn_family = fam
            self.injections.append(Injection(fam, marker, repair))
            return NS(stop_reason="end_turn", content=[NS(type="text", text=new)])
        return resp

    # ------------------------------------------------------------------ helpers

    def _place(self, text: str, sentence: str) -> str:
        """Put a new sentence at the start or the end of the answer."""
        text = text.rstrip()
        if self.rng.random() < 0.3:
            return f"{sentence} {text}"
        return f"{text} {sentence}"

    def _focus(self, ev: Evidence) -> tuple[dict, str] | None:
        recs = [(r, rid) for r in ev.records if (rid := _record_id(r))]
        return self.rng.choice(recs) if recs else None

    def _money(self, amt: str, currency: str = "USD") -> str:
        return self.rng.choice([f"{amt} {currency}", f"{currency} {amt}", f"${amt}" if currency == "USD" else
                                f"{amt} {currency}", amt])

    # ------------------------------------------------------------------ families

    def _amount_perturb(self, text: str, ev: Evidence):
        """A real amount from the answer, changed slightly: digits transposed, cents off, a place shifted."""
        found = [m for m in AMOUNT.finditer(text) if m.group(0).replace(",", "") in ev.blob]
        if not found:
            return None
        m = self.rng.choice(found)
        cents = int(round(float(m.group(0).replace(",", "")) * 100))
        how = self.rng.choice(["transpose", "cents", "units", "shift"])
        if how == "transpose" and cents >= 10:
            s = str(cents)
            i = self.rng.randrange(len(s) - 1)
            new = int(s[:i] + s[i + 1] + s[i] + s[i + 2:])
        elif how == "cents":
            new = cents + self.rng.choice([-1, 1]) * self.rng.randint(1, 9)
        elif how == "units":
            new = cents + self.rng.choice([-1, 1]) * 100 * self.rng.randint(1, 20)
        else:
            new = cents * 10
        if new <= 0 or new == cents:
            new = cents + 7
        val = f"{new // 100}.{new % 100:02d}"
        return text[:m.start()] + val + text[m.end():], val

    def _date_shift(self, text: str, ev: Evidence):
        """A real date from the answer moved by a few days, sometimes written in words."""
        found = [m for m in ISO_DATE.finditer(text) if m.group(0) in ev.blob]
        if not found:
            return None
        m = self.rng.choice(found)
        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        d += dt.timedelta(days=self.rng.choice([-1, 1]) * self.rng.randint(1, 30))
        style = self.rng.choice(["iso", "long", "short", "dmy"])
        val = {"iso": d.isoformat(), "long": f"{MONTHS[d.month - 1]} {d.day}, {d.year}",
               "short": f"{MONTHS[d.month - 1][:3]} {d.day}", "dmy": f"{d.day} {MONTHS[d.month - 1]} {d.year}"}[style]
        return text[:m.start()] + val + text[m.end():], val

    def _code_swap(self, text: str, ev: Evidence):
        """The record's decline code or reason key replaced by another real one from the same vocabulary."""
        from .domains import DOMAINS
        from .policy import POLICY
        vocab = [set(POLICY)] + [set(d.policy) for d in DOMAINS.values()]
        for rec in ev.records:
            for key in ("decline_code", "reason_key", "return_code", "error", "restriction", "reason_code"):
                code = rec.get(key)
                if not isinstance(code, str) or not re.search(rf"\b{re.escape(code)}\b", text):
                    continue
                pool = sorted(next((v for v in vocab if code in v), set()) - {code})
                pool = [c for c in pool if not re.search(rf"\b{re.escape(c)}\b", ev.blob)]
                if not pool:
                    continue
                new = self.rng.choice(pool)
                m = re.search(rf"(\b\w+\s+)?\b{re.escape(code)}\b", text)
                if m is None:
                    continue
                lead = m.group(1) or ""
                return text[:m.start()] + lead + new + text[m.end():], (lead + new).strip()
        return None

    def _status_paraphrase(self, text: str, ev: Evidence):
        """A status this record does not have, said in one of several ways."""
        f = self._focus(ev)
        if f is None:
            return None
        rec, rid = f
        status = str(rec.get("status", ""))
        dom = _domain_of(rid)
        if dom is None:                                     # card payment
            if status == "approved":
                phrase = self.rng.choice(PAY_NEGATIVE)
            elif status in ("declined", "failed"):
                phrase = self.rng.choice(PAY_POSITIVE)
            else:
                phrase = self.rng.choice(PAY_POSITIVE + PAY_NEGATIVE)
            sentence = self.rng.choice(STATUS_TEMPLATES).format(id=rid, phrase=phrase, noun="payment")
        else:
            words = [w for w, v in dom.statuses.items() if v != status
                     and v not in dom.implied.get(status, frozenset())
                     and status not in dom.implied.get(v, frozenset())]
            if not words:
                return None
            word = self.rng.choice(words)
            sentence = self.rng.choice(DOMAIN_STATUS_TEMPLATES).format(id=rid, word=word, noun=dom.noun)
        return self._place(text, sentence), sentence.rstrip(".")

    def _cause_paraphrase(self, text: str, ev: Evidence):
        """A cause the record does not carry, in the checks' words or in other words."""
        from .reasons import KEY_CAUSE
        f = self._focus(ev)
        if f is None or _domain_of(f[1]) is not None or f[0].get("status") not in ("declined", "failed"):
            return None
        true = {KEY_CAUSE.get(str(f[0].get(k, ""))) for k in ("decline_code", "error")}
        pool = [c for c in CAUSE_WORDS if c not in true]
        cause = self.rng.choice(pool)
        words = self.rng.choice(CAUSE_WORDS[cause])
        sentence = self.rng.choice(["It was declined {w}.", "This happened {w}.", "The payment failed {w}."]) \
            .format(w=words)
        return self._place(text, sentence), words

    def _invented_amount(self, text: str, ev: Evidence):
        amt = f"{self.rng.randint(1, 499)}.{self.rng.randint(0, 99):02d}"
        cur = next((str(r["currency"]) for r in ev.records if isinstance(r.get("currency"), str)), "USD")
        sentence = self.rng.choice(INVENTED_AMOUNT).format(kind=self.rng.choice(FEE_KINDS),
                                                           amt=self._money(amt, cur))
        return self._place(text, sentence), amt

    def _invented_contact(self, text: str, ev: Evidence):
        kind = self.rng.choice(["phone", "email", "url", "wiki_url"])
        n = self.rng.randint(1000, 9999)
        if kind == "phone":
            val = f"1-8{self.rng.choice('0045678')}{self.rng.randint(0, 9)}-555-{n}"
            sentence = f"You can call the payments desk on {val}."
        elif kind == "email":
            val = f"{self.rng.choice(['help', 'billing', 'disputes', 'refunds'])}{n}@" \
                  f"{self.rng.choice(['support-desk', 'pay-help', 'cardcare'])}.example"
            sentence = f"Email {val} and they'll sort it out."
        elif kind == "url":
            host, path = self.rng.choice(["help", "status", "docs"]), self.rng.choice(["kb", "faq"])
            val = f"https://{host}.example.com/{path}/{n}"
            sentence = f"More details: {val}"
        else:                                        # a page id on the real help-site host
            val = f"https://wiki.example.com/pages/{n}"
            sentence = f"(source: {val})"
        return self._place(text, sentence), val

    def _invented_id(self, text: str, ev: Evidence):
        f = self._focus(ev)
        prefix = f[1].split("_")[0] if f else "txn"
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
        val = f"{prefix}_" + "".join(self.rng.choice(alphabet) for _ in range(len(f[1].split('_')[1]) if f else 10))
        sentence = self.rng.choice(["This looks related to {v}.", "It was linked to {v} on the same day.",
                                    "The same thing happened on {v}."]).format(v=val)
        return self._place(text, sentence), val

    def _promise_paraphrase(self, text: str, ev: Evidence):
        sentence = self.rng.choice(PROMISES).format(n=self.rng.randint(2, 10), day=self.rng.choice(DAYS))
        return self._place(text, sentence), sentence.rstrip(".")


# ---------------------------------------------------------------- scoring

@dataclass
class FamilyStats:
    injected: int = 0      # first drafts corrupted
    persisted: int = 0     # repair drafts corrupted again
    repaired: int = 0      # the user saw a verified model answer
    records_only: int = 0  # the user saw the answer code built from the record
    refused: int = 0       # the user saw "I won't guess" (or another withheld message)
    leaked: int = 0        # a marker reached the user
    examples: list[str] = field(default_factory=list)

    def row(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "examples"}


def score_turn(turn, new: list[Injection], stats: dict[str, FamilyStats], keep_examples: int = 3) -> None:
    """Attribute one turn's outcome to the family injected into its first draft."""
    first = next((i for i in new if not i.repair), None)
    if first is None:
        return
    s = stats.setdefault(first.family, FamilyStats())
    s.injected += 1
    s.persisted += any(i.repair for i in new)
    answer = turn.answer.lower()
    leaked = [i for i in new if i.marker.lower() in answer]
    if leaked:
        s.leaked += 1
        if len(s.examples) < keep_examples:
            where = "repair" if leaked[0].repair else "first draft"
            s.examples.append(f"{leaked[0].marker!r} ({where}) in: {turn.answer[:240]}")
    elif turn.blocked_by:
        s.refused += 1
    elif any(str(r).startswith("records_only:") for r in turn.reasons):
        s.records_only += 1
    else:
        s.repaired += 1
