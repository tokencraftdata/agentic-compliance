"""Hallucination gate: verify -> (bounded repair) -> verify, as one component.

    Verifier          strategy: check(answer, tool_log, focus) -> GuardResult
    VerifierChain     runs every verifier, merges reasons (chain of responsibility;
                      a block from any verifier blocks the answer)
    HallucinationGate owns the repair policy: on block, ask the model ONCE to
                      rewrite using only tool facts, re-verify against the repair's
                      own tool results, then allow or fall back.

The orchestrator only calls gate.run(...). Adding a check (e.g. a Claude-as-judge
verifier for free-text policy answers, or a numeric reconciliation check) is a
new Verifier class, with no change to the turn pipeline.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from .adaptive.policy import GuardPolicy
from .guardrails import BLOCKED_OUTPUT_MSG, GuardResult, evidence_check


class Verifier(Protocol):
    name: str

    def check(self, answer: str, tool_log: list[dict], focus: str | None) -> GuardResult: ...


class EvidenceVerifier:
    """Deterministic grounding: amounts, codes, ids, URLs, status polarity,
    unknown-outcome and lifecycle claims, retry advice vs policy, processor routing claims."""
    name = "evidence"

    def __init__(self, known_processors: set[str] | None = None):
        self.known_processors = known_processors

    def check(self, answer, tool_log, focus):
        return evidence_check(answer, tool_log, focus, self.known_processors)


class PolicyVerifier:
    """The learned commitment rules of the guard policy serving this turn: promises
    the bot can't keep ("you'll get a refund", "guaranteed"). A violation gets the
    same one repair as an ungrounded fact."""
    name = "policy"

    def __init__(self, policy: Callable[[], GuardPolicy]):
        self.policy = policy

    def check(self, answer, tool_log, focus):
        reasons = self.policy().answer_violations(answer)
        return GuardResult("block", BLOCKED_OUTPUT_MSG, reasons) if reasons else GuardResult("allow", answer)


TICKET_ID = re.compile(r"\bTCK-[A-Z0-9]{6,}\b")
TICKET_CLAIM = re.compile(r"\b(i|we)(\s+have|'ve|\s+just)?\s+(raised|created|opened|logged|filed|submitted)\b[^.]{0,40}"
                          r"\b(ticket|case|request)\b", re.I)


class TicketVerifier:
    """Only code opens tickets. An answer may mention a ticket id only if this session
    has it, and may say "I've raised a ticket" only if one exists."""
    name = "tickets"

    def __init__(self, known: Callable[[], set[str]]):
        self.known = known

    def check(self, answer, tool_log, focus):
        known = self.known()
        reasons = [f"ungrounded_ticket:{t}" for t in sorted(set(TICKET_ID.findall(answer)) - known)]
        if not known and TICKET_CLAIM.search(answer):
            reasons.append("claims_ticket_created")
        return GuardResult("block", BLOCKED_OUTPUT_MSG, reasons) if reasons else GuardResult("allow", answer)


class VerifierChain:
    def __init__(self, verifiers: list[Verifier]):
        self.verifiers = verifiers

    def check(self, answer, tool_log, focus) -> GuardResult:
        reasons: list[str] = []
        for v in self.verifiers:
            r = v.check(answer, tool_log, focus)
            if r.action == "block":
                reasons += r.reasons
        return GuardResult("block", BLOCKED_OUTPUT_MSG, reasons) if reasons else GuardResult("allow", answer)


REPAIR_PROMPT = ("<verifier>Your previous answer was NOT shown to the user because these checks failed: "
                 "{reasons}. Rewrite the answer using only facts from tool results (call tools again if "
                 "needed), and don't promise outcomes the records don't show. If the data is missing, say so."
                 "</verifier>")


@dataclass
class GateResult:
    action: str                 # allow | block
    answer: str                 # verified answer, or the fallback message
    reasons: list[str]
    repaired: bool = False
    tool_log: list[dict] = field(default_factory=list)  # all tool calls incl. the repair's


class HallucinationGate:
    def __init__(self, chain: VerifierChain | None = None, repair_attempts: int = 1):
        self.chain = chain or VerifierChain([EvidenceVerifier()])
        self.repair_attempts = repair_attempts

    def run(self, answer: str, tool_log: list[dict], focus: Callable[[], str | None],
            regenerate: Callable[[str], tuple[str, list[dict]]]) -> GateResult:
        """regenerate(feedback) -> (new_answer, new_tool_log); it runs the model loop
        on the strong tier with the feedback appended to the history."""
        log = list(tool_log)
        g = self.chain.check(answer, log, focus())
        if g.action == "allow":
            return GateResult("allow", answer, g.reasons, False, log)
        first = g.reasons
        for _ in range(self.repair_attempts):
            answer, retry_log = regenerate(REPAIR_PROMPT.format(reasons=", ".join(g.reasons)))
            log += retry_log
            # Verify against the repair's own tool results when it re-fetched, so a stale
            # earlier result cannot vouch for a claim the repair made.
            g = self.chain.check(answer, retry_log or log, focus())
            if g.action == "allow":
                return GateResult("allow", answer, [f"repaired:{r}" for r in first], True, log)
        return GateResult("block", g.text, g.reasons, False, log)
