"""The gate: a proposal ships only if it fixes what it claims and breaks nothing.

For each proposal, against the policy it would change:

  1. its own regression cases pass (each labelled text gets the decision the
     reviewer asked for);
  2. a new input rule matches none of the benign corpus (fair questions from
     the eval sets and the benign red-team look-alikes);
  3. every suite is replayed with the current and the candidate policy, and no
     case that passed before fails after: case-level non-regression, not just
     an equal score.

Suites:
    CaseSuite      direct policy decisions on hand-written guard cases (fast)
    EvalSuite      a labelled eval set through the whole bot (dev, held-out)
    RedTeamSuite   the prompt-injection set with the worst-case model, defended
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .policy import GuardPolicy, PolicyError
from .proposers import GuardCase, Proposal, decides


@dataclass(frozen=True)
class SuiteResult:
    name: str
    passed: frozenset[str]
    failed: frozenset[str]


class Suite(Protocol):
    name: str

    def run(self, policy: GuardPolicy) -> SuiteResult: ...


class CaseSuite:
    def __init__(self, name: str, cases: Sequence[GuardCase]):
        self.name, self.cases = name, list(cases)

    def run(self, policy: GuardPolicy) -> SuiteResult:
        ok = {c.id for c in self.cases if decides(policy, c) == c.expect}
        return SuiteResult(self.name, frozenset(ok), frozenset(c.id for c in self.cases) - frozenset(ok))


class EvalSuite:
    """A labelled eval set (data/eval.jsonl format) through the full bot."""

    def __init__(self, name: str, eval_path: str, data_dir: str):
        self.name, self.eval_path, self.data_dir = name, eval_path, data_dir

    def run(self, policy: GuardPolicy) -> SuiteResult:
        from ..config import Settings
        from ..container import Container
        from ..evalkit import load_cases, run_case
        s = Settings(llm_provider="fake", guardrail_provider="local", data_dir=self.data_dir,
                     kb_path="/nonexistent")
        ok: set[str] = set()
        bad: set[str] = set()
        for case in load_cases(self.eval_path):
            def bot() -> Any:
                b = Container(s, overrides={"guard_policy": policy}).chatbot
                b.sleep = b.toolbox.sleep = lambda _s: None
                return b
            fails, _, _ = run_case(bot, case)
            (bad if fails else ok).add(case["id"])
        return SuiteResult(self.name, frozenset(ok), frozenset(bad))


class RedTeamSuite:
    def __init__(self, name: str, cases_path: str):
        self.name, self.cases_path = name, cases_path

    def run(self, policy: GuardPolicy) -> SuiteResult:
        from ..evalkit import load_cases
        from ..redteam import run_case
        ok: set[str] = set()
        bad: set[str] = set()
        for case in load_cases(self.cases_path):
            (ok if run_case(case, "defended", policy)["ok"] else bad).add(case["id"])
        return SuiteResult(self.name, frozenset(ok), frozenset(bad))


@dataclass
class GateReport:
    proposal: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    suites: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"proposal": self.proposal, "passed": self.passed, "reasons": self.reasons, "suites": self.suites}


class Gate:
    def __init__(self, suites: Sequence[Suite], benign_corpus: Sequence[str] = ()):
        self.suites, self.benign = list(suites), list(benign_corpus)
        self._cache: dict[tuple[str, str], SuiteResult] = {}

    def _run(self, suite: Suite, policy: GuardPolicy) -> SuiteResult:
        key = (suite.name, repr(policy.to_dict()))   # content, not version: two drafts can share a number
        if key not in self._cache:
            self._cache[key] = suite.run(policy)
        return self._cache[key]

    def evaluate(self, proposal: Proposal, current: GuardPolicy) -> GateReport:
        rep = GateReport(proposal.id, False)
        try:
            candidate = proposal.apply(current)
        except (PolicyError, KeyError) as e:
            rep.reasons.append(f"not applicable: {e}")
            return rep
        own = CaseSuite("proposal", proposal.cases).run(candidate)
        if own.failed:
            rep.reasons.append(f"does not fix its own evidence: {sorted(own.failed)}")
        if proposal.rule is not None and proposal.rule.kind != "commitment":
            hits = [t for t in self.benign if proposal.rule.matches(t.lower())]
            if hits:
                rep.reasons.append(f"matches {len(hits)} benign message(s), e.g. {hits[0][:80]!r}")
        if rep.reasons:   # cheap checks failed: skip the expensive replays
            return rep
        for suite in self.suites:
            before, after = self._run(suite, current), self._run(suite, candidate)
            regressed = sorted(before.passed - after.passed)
            total = len(before.passed | before.failed)
            rep.suites[suite.name] = {"before": len(before.passed), "after": len(after.passed), "total": total,
                                      "regressed": regressed}
            if regressed:
                rep.reasons.append(f"{suite.name}: {len(regressed)} case(s) regressed, e.g. {regressed[0]}")
        rep.passed = not rep.reasons
        return rep


def benign_corpus(eval_paths: Sequence[str], redteam_path: str | None = None,
                  extra: Callable[[], Sequence[str]] | None = None) -> list[str]:
    """User messages the bot must keep answering: eval turns expected to pass the
    input guard, and benign red-team look-alikes."""
    from ..evalkit import load_cases
    out: list[str] = []
    for p in eval_paths:
        for c in load_cases(p):
            exp = c.get("expect", {})
            if exp.get("blocked_by") is None and c.get("category") not in ("prompt_attack", "denied_topic"):
                out.extend(c["turns"])
    if redteam_path:
        out.extend(t for c in load_cases(redteam_path) if c.get("benign") for t in c["turns"])
    if extra:
        out.extend(extra())
    return out
