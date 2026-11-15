"""Adaptive guardrail controller: learn from reviewed mistakes, ship nothing unchecked.

    signals ──► proposers ──► gate ──► shadow ──► canary ──► active
       ▲                                                        │
       └──────────────── audit: drift, shadow divergence ◄──────┘

Lifecycle of a change:

    proposed        a proposer turned reviewer labels into a Proposal
    rejected        the gate failed it (reasons and suite numbers are kept)
    report          a false positive on a BUILT-IN or baseline guard: goes to
                    engineering as a code change, never applied automatically
    awaiting_approval  a loosening change that passed the gate: an admin must approve
    shadow          applied to the shadow policy: evaluated on live traffic, logged, not enforced
    canary/active   an admin promoted the shadow policy

Rules this enforces:
  * The controller only edits LEARNED rules in a GuardPolicy. Built-in guards
    (prompt-attack screen, PII, evidence checks, retry and cascade policy) are code.
  * Tightening may reach SHADOW automatically. Nothing reaches users without an
    admin promote, and loosening needs an admin approve first.
  * Promotion needs `min_shadow_turns` of shadow traffic, so a rule is seen on
    real messages before it acts on them.
  * Every write is audit-logged with who, what, why, when; every policy version
    is kept, so rollback is one call.
  * No model weights are trained here, and no user text becomes a rule
    without a reviewer's label.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from .. import auditlog
from ..security import Authorizer, Principal
from .gate import Gate
from .policy import BASELINE, GuardPolicy
from .proposers import Proposal, Proposer
from .signals import Label, LabelLog, rule_usage, shadow_stats, version_stats
from .source import CONTROL_KEY, ControlStore

PROPOSALS_KEY = "guard_proposals"
MAX_HISTORY = 20


class GuardrailController:
    def __init__(self, store: ControlStore, gate: Gate, proposers: Sequence[Proposer], labels: LabelLog,
                 authorizer: Authorizer | None = None, audit_path: str | None = None,
                 turn_audit_path: str | None = None, clock: Callable[[], float] = time.time,
                 min_shadow_turns: int = 200):
        self.store, self.gate, self.proposers, self.labels = store, gate, list(proposers), labels
        self.authz = authorizer or Authorizer()
        self.audit_path, self.turn_audit_path = audit_path, turn_audit_path
        self.clock, self.min_shadow_turns = clock, min_shadow_turns

    # ------------------------------------------------------------------ state

    def _state(self) -> dict[str, Any]:
        s = self.store.read_control(CONTROL_KEY) or {}
        return {"active": s.get("active") or BASELINE.to_dict(), "shadow": s.get("shadow"),
                "canary": s.get("canary"), "history": list(s.get("history", []))}

    def _proposals(self) -> dict[str, Any]:
        p = self.store.read_control(PROPOSALS_KEY) or {}
        return {"items": dict(p.get("items", {})), "used_labels": list(p.get("used_labels", []))}

    def active(self) -> GuardPolicy:
        return GuardPolicy.from_dict(self._state()["active"])

    def _working(self, state: dict[str, Any]) -> GuardPolicy:
        """The policy new changes stack on: shadow if one is in flight, else active."""
        return GuardPolicy.from_dict(state["shadow"] or state["active"])

    def _next_version(self, state: dict[str, Any]) -> int:
        seen = [state["active"]["version"], *(h["version"] for h in state["history"])]
        if state["shadow"]:
            seen.append(state["shadow"]["version"])
        if state["canary"]:
            seen.append(state["canary"]["policy"]["version"])
        return int(max(seen)) + 1

    # ------------------------------------------------------------------ reviewers

    def label(self, who: Principal, label: str, text: str, phrase: str = "", session: str = "") -> Label:
        self.authz.can_review(who)
        rec = self.labels.add(label, text, phrase, who.subject, session)
        self._audit(who, "label", label_id=rec.id, label=label)
        return rec

    # ------------------------------------------------------------------ the loop

    def run_cycle(self, who: Principal, reason: str = "scheduled cycle") -> dict[str, Any]:
        """Labels -> proposals -> gate. Passing tightenings stack onto the shadow policy."""
        self.authz.can_control(who)
        state, props = self._state(), self._proposals()
        used = set(props["used_labels"])
        fresh = [lb for lb in self.labels.all() if lb.id not in used]
        working = self._working(state)
        changed = False
        summary: dict[str, list[str]] = {"shadow": [], "awaiting_approval": [], "rejected": [], "report": []}
        for proposer in self.proposers:
            for p in proposer.propose(fresh, working):
                if p.id in props["items"]:
                    continue
                if p.op == "report":
                    status, report = "report", None
                else:
                    report = self.gate.evaluate(p, working)
                    if not report.passed:
                        status = "rejected"
                    elif p.direction == "loosen":
                        status = "awaiting_approval"
                    else:
                        working = p.apply(working)
                        status, changed = "shadow", True
                props["items"][p.id] = {"proposal": p.to_dict(), "status": status, "ts": self.clock(),
                                        "gate": report.to_dict() if report else None}
                summary[status].append(p.id)
                used.update(p.evidence)
        props["used_labels"] = sorted(used)
        self.store.write_control(PROPOSALS_KEY, props)
        if changed:
            state["shadow"] = {**working.to_dict(), "version": self._next_version(state)}
            self.store.write_control(CONTROL_KEY, state)
        self._audit(who, "cycle", reason=reason, labels=len(fresh), **summary,
                    shadow_version=state["shadow"]["version"] if state["shadow"] else None)
        return {"labels": len(fresh), **summary,
                "shadow_version": state["shadow"]["version"] if state["shadow"] else None}

    def approve(self, who: Principal, proposal_id: str, reason: str) -> dict[str, Any]:
        """Approve a loosening change: re-gated against the current working policy, then to shadow."""
        self.authz.can_control(who)
        if not reason.strip():
            raise ValueError("approving a loosening change needs a reason")
        state, props = self._state(), self._proposals()
        item = props["items"].get(proposal_id)
        if not item or item["status"] != "awaiting_approval":
            raise ValueError(f"{proposal_id} is not awaiting approval")
        p = Proposal.from_dict(item["proposal"])
        working = self._working(state)
        report = self.gate.evaluate(p, working)   # the policy may have moved since the cycle
        item["gate"] = report.to_dict()
        if not report.passed:
            item["status"] = "rejected"
        else:
            state["shadow"] = {**p.apply(working).to_dict(), "version": self._next_version(state)}
            item["status"], item["approved_by"] = "shadow", who.subject
            self.store.write_control(CONTROL_KEY, state)
        self.store.write_control(PROPOSALS_KEY, props)
        self._audit(who, "approve", proposal=proposal_id, reason=reason, result=item["status"])
        return dict(item)

    def reject(self, who: Principal, proposal_id: str, reason: str) -> None:
        self.authz.can_control(who)
        props = self._proposals()
        if proposal_id not in props["items"]:
            raise KeyError(proposal_id)
        props["items"][proposal_id]["status"] = "rejected"
        self.store.write_control(PROPOSALS_KEY, props)
        self._audit(who, "reject", proposal=proposal_id, reason=reason)

    # ------------------------------------------------------------------ rollout

    def promote(self, who: Principal, reason: str, percent: int = 100) -> dict[str, Any]:
        """shadow -> canary (percent < 100) -> active (100). Needs enough shadow traffic."""
        self.authz.can_control(who)
        if not 1 <= percent <= 100:
            raise ValueError("percent must be 1..100")
        state = self._state()
        if state["canary"]:
            candidate = state["canary"]["policy"]
        elif state["shadow"]:
            seen = shadow_stats(self.turn_audit_path, state["shadow"]["version"]).turns
            if seen < self.min_shadow_turns:
                raise ValueError(f"shadow v{state['shadow']['version']} has seen {seen} turns; "
                                 f"promotion needs {self.min_shadow_turns}")
            candidate = state["shadow"]
        else:
            raise ValueError("nothing to promote: no shadow or canary policy")
        if percent < 100:
            state["canary"], state["shadow"] = {"policy": candidate, "percent": percent}, None
        else:
            state["history"] = ([state["active"], *state["history"]])[:MAX_HISTORY]
            state["active"], state["canary"], state["shadow"] = candidate, None, None
        self.store.write_control(CONTROL_KEY, state)
        self._mark(candidate, "canary" if percent < 100 else "active")
        self._audit(who, "promote", version=candidate["version"], percent=percent, reason=reason)
        return state

    def rollback(self, who: Principal, reason: str) -> dict[str, Any]:
        """Undo the most recent step: drop canary, else drop shadow, else restore the previous active."""
        self.authz.can_control(who)
        state = self._state()
        if state["canary"]:
            what, state["canary"] = f"canary v{state['canary']['policy']['version']}", None
        elif state["shadow"]:
            what, state["shadow"] = f"shadow v{state['shadow']['version']}", None
        elif state["history"]:
            what = f"active v{state['active']['version']} -> v{state['history'][0]['version']}"
            state["active"], state["history"] = state["history"][0], state["history"][1:]
        else:
            raise ValueError("nothing to roll back")
        self.store.write_control(CONTROL_KEY, state)
        self._audit(who, "rollback", what=what, reason=reason)
        return state

    # ------------------------------------------------------------------ reads

    def status(self, who: Principal) -> dict[str, Any]:
        self.authz.can_control(who)
        state, props = self._state(), self._proposals()
        by_status: dict[str, list[dict[str, Any]]] = {}
        for pid, item in props["items"].items():
            gate = item.get("gate") or {}
            by_status.setdefault(item["status"], []).append(
                {"id": pid, "rationale": item["proposal"]["rationale"], "reasons": gate.get("reasons", []),
                 "suites": {k: f"{v['after']}/{v['total']}" for k, v in gate.get("suites", {}).items()}})
        out: dict[str, Any] = {
            "active": {"version": state["active"]["version"], "rules": len(state["active"]["rules"])},
            "canary": ({"version": state["canary"]["policy"]["version"], "percent": state["canary"]["percent"]}
                       if state["canary"] else None),
            "shadow": None, "proposals": by_status,
            "drift": {v: s.to_dict() for v, s in sorted(version_stats(self.turn_audit_path).items())},
            "history": [h["version"] for h in state["history"]],
        }
        if state["shadow"]:
            sh = shadow_stats(self.turn_audit_path, state["shadow"]["version"])
            out["shadow"] = {**sh.to_dict(), "needed_for_promotion": self.min_shadow_turns}
        return out

    def upkeep(self, who: Principal, idle_after_turns: int = 1000) -> dict[str, Any]:
        """What needs a person's attention to keep the rules healthy: learned rules that
        never fire (candidates to retire, which is a loosening and needs an admin), work
        waiting on a person, and how much reviewing the traffic is costing."""
        self.authz.can_control(who)
        active, props = self.active(), self._proposals()
        turns, hits = rule_usage(self.turn_audit_path)
        waiting = [pid for pid, item in props["items"].items() if item["status"] in ("awaiting_approval", "report")]
        labels = self.labels.all()
        return {
            "turns": turns,
            "rules": [{"id": r.id, "kind": r.kind, "learned": r.source != "baseline", "hits": hits[r.id]}
                      for r in active.rules],
            "idle_learned_rules": [r.id for r in active.rules if r.source != "baseline" and not hits[r.id]
                                   and turns >= idle_after_turns],
            "waiting_on_a_person": waiting,
            "labels": len(labels),
            "labels_per_1000_turns": round(1000 * len(labels) / turns, 1) if turns else None,
        }

    # ------------------------------------------------------------------ internals

    def _mark(self, policy: dict[str, Any], status: str) -> None:
        props = self._proposals()
        sources = {r["source"] for r in policy["rules"]}
        ids = {r["id"] for r in policy["rules"]}
        for pid, item in props["items"].items():
            p = item["proposal"]
            included = pid in sources if p["op"] == "add_rule" else p["rule_id"] not in ids
            if item["status"] in ("shadow", "canary") and included:
                item["status"] = status
        self.store.write_control(PROPOSALS_KEY, props)

    def _audit(self, who: Principal, op: str, **detail: Any) -> None:
        auditlog.write(self.audit_path, "guard", {"ts": self.clock(), "actor": who.subject, "op": op, **detail})
