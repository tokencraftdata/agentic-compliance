"""The default gate and controller wiring for a data directory (dev, held-out, red team)."""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from ..security import Authorizer
from .controller import GuardrailController
from .gate import CaseSuite, EvalSuite, Gate, RedTeamSuite, Suite, benign_corpus
from .proposers import GuardCase, Proposer, ReviewLabelProposer
from .signals import LabelLog
from .source import ControlStore


def guard_cases(path: str) -> list[GuardCase]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [GuardCase.from_dict(json.loads(line)) for line in f if line.strip()]


def default_gate(data_dir: str = "data") -> Gate:
    """Hand-written guard cases, the dev eval, the held-out eval and the red team."""
    suites: list[Suite] = [CaseSuite("guard_cases", guard_cases(os.path.join(data_dir, "guard_cases.jsonl")))]
    evals = [p for p in (os.path.join(data_dir, "eval.jsonl"),) if os.path.exists(p)]
    for path in evals:
        suites.append(EvalSuite("eval_dev", path, data_dir))
    test_dir = os.path.join(data_dir, "test")
    if os.path.exists(os.path.join(test_dir, "eval.jsonl")):
        evals.append(os.path.join(test_dir, "eval.jsonl"))
        suites.append(EvalSuite("eval_heldout", os.path.join(test_dir, "eval.jsonl"), test_dir))
    redteam = os.path.join(data_dir, "redteam.jsonl")
    if os.path.exists(redteam):
        suites.append(RedTeamSuite("redteam", redteam))
    return Gate(suites, benign_corpus(evals, redteam if os.path.exists(redteam) else None))


def default_controller(store: ControlStore, data_dir: str, labels_path: str, authorizer: Authorizer | None = None,
                       audit_path: str | None = None, turn_audit_path: str | None = None,
                       clock: Callable[[], float] | None = None, min_shadow_turns: int = 200,
                       proposers: list[Proposer] | None = None, **kw: Any) -> GuardrailController:
    import time
    return GuardrailController(store, default_gate(data_dir), proposers or [ReviewLabelProposer()],
                               LabelLog(labels_path, clock or time.time), authorizer, audit_path, turn_audit_path,
                               clock or time.time, min_shadow_turns, **kw)
