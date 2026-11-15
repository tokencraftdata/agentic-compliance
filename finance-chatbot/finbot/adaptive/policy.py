"""Versioned guard policy: the only part of the guardrails the controller may change.

Built-in guards are code and the controller never touches them: the
prompt-attack screen, PII masking, the evidence checks, retry and cascade
policy, tenant scoping. A GuardPolicy holds LEARNED, reviewable rules that run
on top of them:

    input_block    extra prompt-attack or out-of-scope patterns on user text
    escalate       complaints, disputes, vulnerability: the turn goes to a person
    commitment     promises the bot can't keep ("you'll get a refund") in answers
    risk_threshold session risk score at which a session is handed to a person

A rule is a regex with an id, a reason and provenance (the proposal that added
it). Policies are immutable values; every change produces a new version, so a
rollback is "make the previous version active again".
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, replace
from typing import Any, Literal

from ..untrusted import _variants

RuleKind = Literal["input_block", "escalate", "commitment"]
KINDS: tuple[RuleKind, ...] = ("input_block", "escalate", "commitment")
Action = Literal["allow", "block", "escalate"]

MAX_PATTERN = 200
# A group containing a quantifier, itself quantified: "(a+)+", "(\w*)*". Classic
# catastrophic backtracking; a learned rule runs on every message, so refuse it.
NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*[+*{]")


class PolicyError(ValueError):
    pass


@functools.lru_cache(maxsize=512)
def compiled(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


def validate_pattern(pattern: str) -> None:
    if not pattern or len(pattern) > MAX_PATTERN:
        raise PolicyError(f"pattern must be 1..{MAX_PATTERN} characters")
    if NESTED_QUANTIFIER.search(pattern):
        raise PolicyError("nested quantifier: risk of catastrophic backtracking")
    try:
        rx = compiled(pattern)
    except re.error as e:
        raise PolicyError(f"invalid regex: {e}") from e
    if rx.search(""):
        raise PolicyError("pattern matches the empty string")


@dataclass(frozen=True)
class Rule:
    id: str
    kind: RuleKind
    pattern: str
    reason: str = ""
    source: str = "baseline"     # "baseline" or the id of the proposal that added it

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise PolicyError(f"unknown rule kind {self.kind!r}")
        if not re.fullmatch(r"[a-z0-9_]{2,48}", self.id):
            raise PolicyError(f"rule id {self.id!r}: use 2-48 chars of a-z, 0-9, _")
        validate_pattern(self.pattern)

    def matches(self, text: str) -> bool:
        return bool(compiled(self.pattern).search(text))

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "kind": self.kind, "pattern": self.pattern, "reason": self.reason,
                "source": self.source}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Rule:
        return cls(d["id"], d["kind"], d["pattern"], d.get("reason", ""), d.get("source", "baseline"))


@dataclass(frozen=True)
class Decision:
    action: Action
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class GuardPolicy:
    version: int
    rules: tuple[Rule, ...] = ()
    risk_threshold: float = 6.0
    note: str = ""

    def __post_init__(self) -> None:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise PolicyError("duplicate rule id")
        if not 1.0 <= self.risk_threshold <= 50.0:
            raise PolicyError("risk_threshold must be between 1 and 50")

    def of(self, kind: RuleKind) -> tuple[Rule, ...]:
        return tuple(r for r in self.rules if r.kind == kind)

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    # ------------------------------------------------------------------ checks

    def input_decision(self, text: str) -> Decision:
        """Learned rules on user text, over the same decoded views the built-in screen uses."""
        views = [v.lower() for v in _variants(text)]

        def hits(kind: RuleKind) -> tuple[str, ...]:
            return tuple(f"{kind}:{r.id}" for r in self.of(kind) if any(r.matches(v) for v in views))

        if blocked := hits("input_block"):
            return Decision("block", blocked)
        if escalated := hits("escalate"):
            return Decision("escalate", escalated)
        return Decision("allow")

    def answer_violations(self, answer: str) -> list[str]:
        return [f"commitment:{r.id}" for r in self.of("commitment") if r.matches(answer)]

    # ------------------------------------------------------------------ new versions

    def _next(self, rules: tuple[Rule, ...], note: str, **kw: Any) -> GuardPolicy:
        return replace(self, version=self.version + 1, rules=rules, note=note, **kw)

    def with_rule(self, rule: Rule, note: str = "") -> GuardPolicy:
        if any(r.id == rule.id for r in self.rules):
            raise PolicyError(f"rule {rule.id} already exists")
        return self._next((*self.rules, rule), note or f"add {rule.id}")

    def without_rule(self, rule_id: str, note: str = "") -> GuardPolicy:
        self.rule(rule_id)  # KeyError if unknown
        return self._next(tuple(r for r in self.rules if r.id != rule_id), note or f"remove {rule_id}")

    def with_threshold(self, value: float, note: str = "") -> GuardPolicy:
        return self._next(self.rules, note or f"risk_threshold={value:g}", risk_threshold=value)

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "rules": [r.to_dict() for r in self.rules],
                "risk_threshold": self.risk_threshold, "note": self.note}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GuardPolicy:
        return cls(int(d["version"]), tuple(Rule.from_dict(r) for r in d.get("rules", [])),
                   float(d.get("risk_threshold", 6.0)), d.get("note", ""))


# ------------------------------------------------------------------ baseline

_YOU = r"(you|they|we|i)(\s+will|'ll)"

BASELINE = GuardPolicy(1, (
    Rule("complaint", "escalate",
         r"\b(formal complaint|(file|make|raise|lodge) a complaint|ombudsman|report (you|this) to (the )?regulator)\b",
         "complaints go to a person"),
    Rule("unauthorised_payment", "escalate",
         r"\b(i|we) (did ?n[o']t|never) (make|authori[sz]e|recogni[sz]e) (this|that|these|the) "
         r"(payment|charge|transaction|purchase)s?\b",
         "possible fraud victim: dispute or fraud team"),
    Rule("stolen_or_scam", "escalate",
         r"\b(stolen|lost) card\b|\bcard (was|got|has been) stolen\b|\b(i was|i've been|i got) scammed\b",
         "possible fraud victim: fraud team"),
    Rule("hardship", "escalate",
         r"\b(can'?t afford|cannot afford|financial hardship|bereave\w*|passed away|lost my job)\b",
         "possible vulnerable customer"),
    Rule("refund_promise", "commitment",
         _YOU + r"\s+(get|receive|issue|send|be given)\s+(you\s+)?(a\s+|your\s+|the\s+)?(full\s+)?"
                r"(refund|money back|reversal)\b",
         "the bot can't grant refunds"),
    Rule("outcome_promise", "commitment",
         r"\b(we|i)(\s+will|'ll)\s+(refund|reverse|credit|waive|unblock|approve|fix)\b",
         "the bot is read-only and can't act"),
    Rule("guarantee", "commitment", r"\bguarantee(d|s)?\b", "no guarantees about outcomes"),
), note="baseline")
