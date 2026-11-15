"""Signals the controller learns from: reviewer labels and the turn audit.

Reviewer labels are the ONLY thing that can start a rule change. A label is a
person saying "this turn was wrong, in this way":

    missed_attack        the bot answered an attack          -> tighten input_block
    missed_escalation    the bot answered what needed a human -> tighten escalate
    broken_promise       the answer promised an outcome       -> tighten commitment
    false_block          a fair question was blocked          -> loosen (remove a learned rule)
    false_escalation     sent to a human for no reason        -> loosen
    false_commitment     a fine answer was withheld           -> loosen

Raw chat text never becomes a rule on its own: the labelled text is the evidence,
and the reviewer may highlight the exact phrase that matters.

The turn audit gives drift numbers per policy version (block, escalation and
repair rates) and shadow divergences, which an admin reads before promoting.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from typing import Any, Literal

from ..pii import mask_pii
from .policy import RuleKind

LabelName = Literal["missed_attack", "missed_escalation", "broken_promise",
                    "false_block", "false_escalation", "false_commitment"]
LABELS: dict[str, tuple[RuleKind, Literal["tighten", "loosen"]]] = {
    "missed_attack": ("input_block", "tighten"),
    "missed_escalation": ("escalate", "tighten"),
    "broken_promise": ("commitment", "tighten"),
    "false_block": ("input_block", "loosen"),
    "false_escalation": ("escalate", "loosen"),
    "false_commitment": ("commitment", "loosen"),
}
MAX_TEXT = 2000


@dataclass(frozen=True)
class Label:
    id: str
    label: str
    text: str            # the user's message, or the answer for commitment labels (masked on add)
    phrase: str = ""     # optional: the words the reviewer highlighted
    reviewer: str = ""
    session: str = ""
    ts: float = 0.0

    @property
    def kind(self) -> RuleKind:
        return LABELS[self.label][0]

    @property
    def direction(self) -> Literal["tighten", "loosen"]:
        return LABELS[self.label][1]


class LabelLog:
    """Append-only JSONL of reviewer labels."""

    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self.path, self.clock = path, clock

    def add(self, label: str, text: str, phrase: str = "", reviewer: str = "", session: str = "") -> Label:
        if label not in LABELS:
            raise ValueError(f"unknown label {label!r}; known: {', '.join(LABELS)}")
        # Labels are kept and read by people: never card data.
        text, phrase = mask_pii(text.strip())[0][:MAX_TEXT], mask_pii(phrase.strip())[0][:200]
        if not text:
            raise ValueError("label needs the text it is about")
        if phrase and phrase.lower() not in text.lower():
            raise ValueError("the highlighted phrase must appear in the text")
        rec = Label(f"lbl_{uuid.uuid4().hex[:10]}", label, text, phrase, reviewer, session, self.clock())
        with open(self.path, "a") as f:
            f.write(json.dumps(asdict(rec)) + "\n")
        return rec

    def all(self) -> list[Label]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            return [Label(**json.loads(line)) for line in f if line.strip()]


# ------------------------------------------------------------------ audit stats

def _records(path: str | None) -> Iterator[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


@dataclass(frozen=True)
class VersionStats:
    turns: int
    blocked: dict[str, int]
    escalated: int
    repaired: int

    def rate(self, n: int) -> float:
        return round(n / self.turns, 4) if self.turns else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"turns": self.turns, "block_rate": {k: self.rate(v) for k, v in sorted(self.blocked.items())},
                "escalation_rate": self.rate(self.escalated), "repair_rate": self.rate(self.repaired)}


def version_stats(audit_path: str | None) -> dict[int, VersionStats]:
    """Drift view: per policy version, how often turns were blocked, escalated, repaired."""
    turns: Counter[int] = Counter()
    esc: Counter[int] = Counter()
    rep: Counter[int] = Counter()
    blocked: dict[int, Counter[str]] = {}
    for r in _records(audit_path):
        v = r.get("policy")
        if v is None:
            continue
        turns[v] += 1
        esc[v] += bool(r.get("escalated"))
        rep[v] += bool(r.get("repaired"))
        if r.get("blocked_by"):
            blocked.setdefault(v, Counter())[r["blocked_by"]] += 1
    return {v: VersionStats(n, dict(blocked.get(v, {})), esc[v], rep[v]) for v, n in turns.items()}


@dataclass(frozen=True)
class ShadowStats:
    version: int
    turns: int
    diverged: int
    examples: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "turns": self.turns, "diverged": self.diverged,
                "examples": list(self.examples)}


def shadow_stats(audit_path: str | None, version: int, max_examples: int = 5) -> ShadowStats:
    """How a shadow policy would have behaved on real traffic, and where it differs from live."""
    turns = diverged = 0
    examples: list[dict[str, Any]] = []
    for r in _records(audit_path):
        sh = r.get("shadow")
        if not sh or sh.get("version") != version:
            continue
        turns += 1
        if sh.get("diverged"):
            diverged += 1
            if len(examples) < max_examples:
                examples.append({"text": r.get("user_text_masked", "")[:200], "live": r.get("blocked_by"),
                                 "shadow": sh.get("input"), "reasons": sh.get("reasons", [])})
    return ShadowStats(version, turns, diverged, tuple(examples))


def rule_usage(audit_path: str | None) -> tuple[int, Counter[str]]:
    """Upkeep view: turns seen, and how often each rule id fired (as a block, an
    escalation, or a commitment the gate repaired or replaced)."""
    turns, hits = 0, Counter[str]()
    for r in _records(audit_path):
        if r.get("policy") is None:
            continue
        turns += 1
        fired = set()
        for reason in r.get("reasons", []):
            parts = str(reason).split(":")
            for kind in ("input_block", "escalate", "commitment"):
                if kind in parts[:-1]:
                    fired.add(parts[parts.index(kind) + 1])
        hits.update(fired)
    return turns, hits
