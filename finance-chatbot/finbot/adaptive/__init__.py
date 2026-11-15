"""Adaptive guardrails: a versioned guard policy, and a controller that improves
it from reviewed mistakes through a regression gate, shadow mode and a canary.

    policy.py      GuardPolicy, Rule, BASELINE (learned rules; built-in guards stay in code)
    risk.py        session risk score (multi-turn jailbreaks)
    source.py      where turns get their policy: static, or the controller's state
    signals.py     reviewer labels, drift and shadow statistics from the audit log
    proposers.py   labels -> proposals (deterministic, optional Claude drafts)
    gate.py        regression gate: own evidence, benign corpus, suite replays
    controller.py  the lifecycle: cycle, approve, promote, rollback, status
"""
from .controller import GuardrailController
from .gate import CaseSuite, EvalSuite, Gate, GateReport, RedTeamSuite, benign_corpus
from .policy import BASELINE, Decision, GuardPolicy, PolicyError, Rule
from .proposers import ClaudeProposer, GuardCase, Proposal, ReviewLabelProposer
from .signals import LABELS, Label, LabelLog
from .source import PolicySource, StaticPolicySource, StorePolicySource

__all__ = ["BASELINE", "LABELS", "CaseSuite", "ClaudeProposer", "Decision", "EvalSuite", "Gate", "GateReport",
           "GuardCase", "GuardPolicy", "GuardrailController", "Label", "LabelLog", "PolicyError", "PolicySource",
           "Proposal", "RedTeamSuite", "ReviewLabelProposer", "Rule", "StaticPolicySource", "StorePolicySource",
           "benign_corpus"]
