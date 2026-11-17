"""Security gateway: one place that decides on every tool call before it runs, watches the
agent's behaviour across a session, and writes one stream of security events.

    decide(call)      before a tool runs:
                        1. the tool exists and its arguments match its schema   -> deny
                        2. deployment rules (tool, role, effect)                -> deny | approve
                        3. bulk access: too many distinct records in one turn   -> approve
                      "approve" means a person must approve first: the call is held, an approval
                      ticket is opened, and the same call runs once the ticket is resolved "done"
    observe(call)     after a tool ran (or was refused): feeds the behaviour monitor
    BehaviorMonitor   adds to the session risk score on signals an honest session rarely sends:
                      ids refused by provenance, scope denials, invalid arguments, injected text
                      found in a tool result, enumeration of record ids that do not exist,
                      held calls. At the guard policy's threshold the existing risk lock hands
                      the session to a person and keeps it locked.
    SecurityEvents    one JSON line per security-relevant decision, in a shape a SIEM can map
                      (OCSF-style: class, severity, actor, tenant, resource, decision, reasons).
                      Values are never logged: the session id is hashed, arguments are hashed,
                      reasons are codes.

Grants (agent_identity.py) and argument provenance (orchestrator._provenance) stay where they
are; the gateway reads their refusals as signals, so every layer reports to one place.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from . import auditlog

# ------------------------------------------------------------------ events

SEVERITY = {"info": 1, "low": 2, "medium": 3, "high": 4, "critical": 5}


def _h(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


class SecurityEvents:
    """Writes security events to the audit stream "security" (or keeps them, for tests)."""

    def __init__(self, audit_path: str | None = None, keep: bool = False, clock: Callable[[], float] = time.time):
        self.audit_path, self.keep, self.clock = audit_path, keep, clock
        self.events: list[dict] = []

    def emit(self, event_class: str, severity: str, *, tenant: str, session_id: str, subject: str = "",
             resource: str = "", decision: str = "", reasons: Iterable[str] = (), **extra: Any) -> dict:
        ev = {"ts": self.clock(), "class": event_class, "severity": severity,
              "severity_id": SEVERITY[severity], "actor": {"user": subject, "agent": "finbot-agent"},
              "tenant": tenant, "session": _h(session_id), "resource": resource, "decision": decision,
              "reasons": sorted({str(r).split(":", 1)[0] for r in reasons}), **extra}
        if self.keep:
            self.events.append(ev)
        auditlog.write(self.audit_path, "security", ev)
        return ev


# ------------------------------------------------------------------ policy

@dataclass(frozen=True)
class ToolRule:
    """A deployment rule. `tool` and `roles` may use "*". Effects: deny, approve."""
    id: str
    tool: str
    effect: str
    reason: str
    roles: tuple[str, ...] = ("*",)

    def matches(self, tool: str, role: str) -> bool:
        return self.tool in ("*", tool) and ("*" in self.roles or role in self.roles)

    @staticmethod
    def parse(d: dict) -> ToolRule:
        if d.get("effect") not in ("deny", "approve"):
            raise ValueError(f"rule {d.get('id')!r}: effect must be deny or approve")
        return ToolRule(str(d["id"]), str(d.get("tool", "*")), d["effect"], str(d.get("reason", "")),
                        _roles(d.get("roles", ["*"])))


def _roles(value) -> tuple[str, ...]:
    """A role list, or one role written as a plain string (tuple("customer") would split it)."""
    if isinstance(value, str):
        return (value,)
    return tuple(str(r) for r in value)


@dataclass
class Decision:
    effect: str                          # allow | deny | approve
    reasons: list[str] = field(default_factory=list)
    ticket: str | None = None


def _type_ok(kind: str | None, v: Any) -> bool:
    if kind == "string":
        return isinstance(v, str)
    if kind == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    return True


def _check_args(schema: dict | None, args: dict) -> list[str]:
    """The arguments a model sent must fit the tool's JSON schema: known keys, required keys,
    string patterns, enums, integer types. Anything else is refused before the tool runs."""
    if schema is None:
        return ["unknown_tool"]
    props = schema.get("properties", {})
    reasons = [f"unexpected_argument:{k}" for k in args if k not in props]
    reasons += [f"missing_argument:{k}" for k in schema.get("required", []) if k not in args]
    for k, v in args.items():
        spec = props.get(k, {})
        kind = spec.get("type")
        if not _type_ok(kind, v):
            reasons.append(f"bad_type:{k}")
        elif isinstance(v, str) and spec.get("pattern") and not re.fullmatch(spec["pattern"].strip("^$"), v):
            reasons.append(f"bad_pattern:{k}")
        elif "enum" in spec and v not in spec["enum"]:
            reasons.append(f"bad_value:{k}")
        elif isinstance(v, str) and len(v) > 2000:
            reasons.append(f"too_long:{k}")
    return reasons


# ------------------------------------------------------------------ behaviour

SIGNALS = {                    # risk added per occurrence
    "provenance": 1.5,         # tried an id the user never typed
    "authz": 1.5,              # tried a tool outside the user's scopes
    "invalid_arguments": 1.0,  # sent arguments the schema does not allow
    "injection_in_tool_result": 0.0,   # logged, but a third party wrote it: not the user's risk
    "enumeration": 2.0,        # each new id, from the third on, that does not exist
    "held_for_approval": 0.5,
}


class BehaviorMonitor:
    def __init__(self, enumeration_after: int = 3):
        self.enumeration_after = enumeration_after

    def signals(self, memory, name: str, args: dict, result: dict) -> list[str]:
        """Call BEFORE the result is added to session memory, so a repeat lookup of an id that
        is already known missing is not counted again."""
        from .domains import id_of
        out = []
        status, source = result.get("status"), result.get("source")
        if status == "refused" and source in ("provenance", "authz", "approval", "policy"):
            out.append({"approval": "held_for_approval", "policy": "invalid_arguments"}.get(source, source))
        if [s for s in result.get("screened") or [] if not s.endswith("screening_unavailable")]:
            out.append("injection_in_tool_result")
        rid = id_of(args)
        if status == "not_found" and rid:
            missing = {k for k, e in memory.entities.items() if e.not_found}
            if rid not in missing and len(missing) + 1 >= self.enumeration_after:
                out.append("enumeration")
        return out


# ------------------------------------------------------------------ gateway

class SecurityGateway:
    def __init__(self, schemas: Callable[[], list[dict]], tickets, events: SecurityEvents,
                 rules: Iterable[ToolRule] = (), max_records_per_turn: int = 3,
                 monitor: BehaviorMonitor | None = None):
        self.schemas, self.tickets, self.events = schemas, tickets, events
        self.rules = sorted(rules, key=lambda r: r.effect != "deny")   # a deny always wins over approve
        self.max_records_per_turn = max_records_per_turn
        self.monitor = monitor or BehaviorMonitor()

    def _schema(self, tool: str) -> dict | None:
        return next((s["input_schema"] for s in self.schemas() if s["name"] == tool), None)

    def decide(self, cs, tool: str, args: dict, turn_calls: list[dict]) -> Decision:
        from .domains import id_of
        bad = _check_args(self._schema(tool), args)
        if bad:
            return Decision("deny", bad)
        role = cs.session.role
        for r in self.rules:
            if r.matches(tool, role):
                if r.effect == "deny":
                    return Decision("deny", [f"rule:{r.id}"])
                return self._approval(cs, tool, args, f"rule:{r.id}", r.reason)
        rid = id_of(args)
        if rid is not None:      # any tool that reads one record by id (records, logs, routing traces)
            # Count only records actually read: a call held or refused earlier read nothing, so a
            # retry of it must not count as "already seen" (that would skip the approval).
            seen = {id_of(c["input"]) for c in turn_calls
                    if id_of(c["input"]) is not None and c["result"].get("status") != "refused"}
            if rid not in seen and len(seen) >= self.max_records_per_turn:
                return self._approval(cs, tool, args, "bulk_access",
                                      f"more than {self.max_records_per_turn} records in one turn")
        return Decision("allow")

    def _approval(self, cs, tool: str, args: dict, reason: str, why: str) -> Decision:
        """Hold the call until a person approves it. The ticket id is derived from the session,
        tool and arguments, so asking again reuses the same ticket, and an approved ticket lets
        exactly that call through."""
        from .tickets import DONE, REJECTED, open_ticket
        t, _ = open_ticket(self.tickets, tenant_id=cs.session.tenant_id, session_id=cs.session.session_id,
                           role=cs.session.role, kind=f"approval:{tool}", txn_id=_h(args),
                           text=f"Approve {tool} for this session ({why})", facts={"tool": tool, "args": args},
                           policy_note=why)
        if t.status == DONE:
            return Decision("allow", [f"approved:{t.id}"], t.id)
        if t.status == REJECTED:              # a person said no: stop asking
            return Decision("deny", [f"rejected:{t.id}"], t.id)
        return Decision("approve", [reason], t.id)

    def check(self, cs, tool: str, args: dict, turn_calls: list[dict]) -> dict | None:
        """The refusal envelope to return instead of running the tool, or None to run it."""
        d = self.decide(cs, tool, args, turn_calls)
        base = {"tenant": cs.session.tenant_id, "session_id": cs.session.session_id,
                "subject": cs.session.subject, "resource": tool}
        if d.effect == "allow":
            if d.ticket:
                self.events.emit("tool_call_approved", "info", decision="allow", reasons=d.reasons,
                                 ticket=d.ticket, **base)
            return None
        if d.effect == "deny":
            self.events.emit("tool_call_denied", "medium", decision="deny", reasons=d.reasons, **base)
            return {"status": "refused", "source": "policy", "data": None,
                    "note": "This call is not allowed. Do not retry it; answer from what you have."}
        self.events.emit("tool_call_held", "low", decision="approval_required", reasons=d.reasons,
                         ticket=d.ticket, **base)
        return {"status": "refused", "source": "approval", "data": None, "ticket": d.ticket,
                "note": (f"This lookup needs a person's approval first (ticket {d.ticket}). Tell the user "
                         "it has been sent for approval; do not guess the data.")}

    def locked(self, cs, threshold: float) -> bool:
        return cs.memory.risk >= threshold

    def observe(self, cs, tool: str, args: dict, result: dict, threshold: float) -> list[str]:
        """After a call, before its result enters session memory: add behaviour signals to the
        session risk score and log them."""
        sig = self.monitor.signals(cs.memory, tool, args, result)
        if not sig:
            return []
        before = cs.memory.risk
        cs.memory.risk += sum(SIGNALS[s] for s in sig)
        base = {"tenant": cs.session.tenant_id, "session_id": cs.session.session_id,
                "subject": cs.session.subject, "resource": tool}
        if result.get("source") in ("provenance", "authz"):
            self.events.emit("tool_call_denied", "medium", decision="deny", reasons=[result["source"]], **base)
        if "injection_in_tool_result" in sig:     # reasons: the screen's code, not the field path
            codes = [str(s).split(":", 1)[1] for s in result.get("screened", []) if ":" in str(s)]
            self.events.emit("prompt_injection", "high", decision="removed", reasons=codes, **base)
        if before < threshold <= cs.memory.risk:
            self.events.emit("suspicious_agent_behavior", "high", decision="session_locked", reasons=sig,
                             risk=round(cs.memory.risk, 1), **base)
        return sig


def load_rules(path: str) -> list[ToolRule]:
    if not path:
        return []
    with open(path) as f:
        return [ToolRule.parse(d) for d in json.load(f)]
