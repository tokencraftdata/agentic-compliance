"""Where the turn pipeline gets its guard policy from.

    StaticPolicySource  one fixed policy (default, tests, the gate's replays)
    StorePolicySource   the controller's state in the shared control store,
                        re-read at most every sync_s, like the model engine:

        active   the policy serving traffic
        canary   {policy, percent}: a share of SESSIONS (stable hash of the
                 session id, so a conversation never flips mid-way)
        shadow   evaluated on every turn and logged, never enforced
        history  earlier active versions, for rollback

A missing or unreadable state falls back to the baseline: guards never switch off.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any, Protocol

from .policy import BASELINE, GuardPolicy

CONTROL_KEY = "guard_policy"


class PolicySource(Protocol):
    def for_session(self, session_id: str) -> GuardPolicy: ...

    def shadow(self) -> GuardPolicy | None: ...


def bucket(session_id: str) -> int:
    """0..99, stable per session."""
    return int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) % 100


class StaticPolicySource:
    def __init__(self, policy: GuardPolicy = BASELINE, shadow: GuardPolicy | None = None):
        self.policy, self._shadow = policy, shadow

    def for_session(self, session_id: str) -> GuardPolicy:
        return self.policy

    def shadow(self) -> GuardPolicy | None:
        return self._shadow


class ControlStore(Protocol):
    def read_control(self, key: str) -> dict[str, Any] | None: ...

    def write_control(self, key: str, value: dict[str, Any]) -> None: ...


class StorePolicySource:
    def __init__(self, store: ControlStore, clock: Callable[[], float] = time.time, sync_s: float = 2.0):
        self.store, self.clock, self.sync_s = store, clock, sync_s
        self._at = float("-inf")
        self._active: GuardPolicy = BASELINE
        self._shadow: GuardPolicy | None = None
        self._canary: tuple[GuardPolicy, int] | None = None

    def _sync(self) -> None:
        now = self.clock()
        if now - self._at < self.sync_s:
            return
        self._at = now
        try:
            state = self.store.read_control(CONTROL_KEY) or {}
            self._active = GuardPolicy.from_dict(state["active"]) if state.get("active") else BASELINE
            self._shadow = GuardPolicy.from_dict(state["shadow"]) if state.get("shadow") else None
            c = state.get("canary")
            self._canary = (GuardPolicy.from_dict(c["policy"]), int(c["percent"])) if c else None
        except Exception:  # unreadable state: keep the last good policy, never run unguarded
            return

    def for_session(self, session_id: str) -> GuardPolicy:
        self._sync()
        if self._canary and bucket(session_id) < self._canary[1]:
            return self._canary[0]
        return self._active

    def shadow(self) -> GuardPolicy | None:
        self._sync()
        return self._shadow
