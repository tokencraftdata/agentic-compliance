"""Reconciliation: check that what finbot's own records say matches what is really there.

finbot keeps its session store strongly consistent (compare-and-swap commits). These jobs cover
the places where two stores can drift apart, and report the differences for a person; none of
them changes data.

    reconcile_tickets   a ticket marked "done" by the desk against the system of record: did the
                        record actually change (refund issued, transfer cancelled, card active)?
    reconcile_audit     the turn audit log against committed turns: every committed turn has an
                        audit line; audit lines for turns that never committed (crash, outage,
                        a worker that lost its lease) are named, and re-runs are counted
    reconcile_index     the help-page index against its source: pages missing from the index,
                        indexed at an old version, or still indexed after the source deleted them

Run them from scripts/reconcile.py (nightly, next to ingestion). Unknown-outcome external
actions are reconciled by the worker itself before each turn (runtime.Worker, action_checker).
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import nullcontext
from dataclasses import asdict, dataclass, field

from . import auditlog
from .crypto import session_tenant
from .tenancy import TenantViolation, current_tenant, tenant_scope

# What the record must show once a person has done what the ticket asked. A kind not listed
# here (retry, limit change, fee waiver, ...) cannot be checked from a record: reported as such.
_CANCELLED = {"cancelled", "canceled", "voided", "reversed", "returned", "withdrawn", "closed"}
_ACTIVE = {"active", "open", "current"}


def _refund_done(rec: dict) -> bool:
    status = str(rec.get("refund_status") or "").lower()
    try:
        amount = float(rec.get("refunded_amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    return status in {"refunded", "succeeded", "completed", "partial"} or amount > 0 or \
        str(rec.get("status", "")).lower() in {"refunded", "reversed"}


def _cancel_done(rec: dict) -> bool:
    return str(rec.get("status", "")).lower() in _CANCELLED or rec.get("cancel_at_period_end") is True


def _unblock_done(rec: dict) -> bool:
    return str(rec.get("status", "")).lower() in _ACTIVE


EXPECTED: dict[str, Callable[[dict], bool]] = {"refund": _refund_done, "cancel": _cancel_done,
                                                "unblock": _unblock_done}


@dataclass
class Finding:
    item: str          # ticket id, session id + msg id, or page id
    kind: str
    verdict: str
    detail: str = ""


@dataclass
class Report:
    job: str
    checked: int = 0
    findings: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, f: Finding) -> None:
        self.findings.append(f)
        self.counts[f.verdict] = self.counts.get(f.verdict, 0) + 1

    @property
    def problems(self) -> list[Finding]:
        ok = {"confirmed", "not_verifiable", "rerun", "uncommitted_outage", "uncommitted_cancelled"}
        return [f for f in self.findings if f.verdict not in ok]

    def as_dict(self) -> dict:
        return {"job": self.job, "checked": self.checked, "counts": self.counts,
                "problems": [asdict(f) for f in self.problems]}


# ------------------------------------------------------------------ 1. tickets

def reconcile_tickets(tickets, lookup: Callable[[str, str], dict], audit_path: str | None = None) -> Report:
    """`lookup(tenant_id, record_id)` returns the tool envelope for that record, read with the
    support role in the ticket's tenant. Only tickets resolved "done" are checked; approval
    tickets (held tool calls) are not about a record change and are skipped."""
    from .tickets import DONE
    rep = Report("tickets")
    for t in tickets.all():
        if t.status != DONE or t.kind.startswith("approval:") or not t.txn_id:
            continue
        rep.checked += 1
        check = EXPECTED.get(t.kind)
        if check is None:
            rep.add(Finding(t.id, t.kind, "not_verifiable", "no record field shows this change"))
            continue
        env = lookup(t.tenant_id, t.txn_id)
        if env.get("status") == "not_found":
            rep.add(Finding(t.id, t.kind, "record_missing", t.txn_id))
        elif env.get("status") != "found":
            rep.add(Finding(t.id, t.kind, "lookup_error", str(env.get("status"))))
        elif check(env.get("data") or {}):
            rep.add(Finding(t.id, t.kind, "confirmed"))
        else:
            observed = (env.get("data") or {}).get("status")
            rep.add(Finding(t.id, t.kind, "mismatch", f"marked done, record status is {observed!r}"))
    for f in rep.problems:
        auditlog.write(audit_path, "reconciliation", {"job": "tickets", **asdict(f)})
    return rep


def toolbox_lookup(toolbox) -> Callable[[str, str], dict]:
    """A record lookup for reconcile_tickets through the normal tool path: tenant-scoped, field
    allow-list, grants. The reconciler reads as the support role; it never writes."""
    from .domains import by_id
    from .tools import Session

    def lookup(tenant_id: str, record_id: str) -> dict:
        run = toolbox.handler(Session("reconciler", tenant_id, ["support"], "support", "reconciler"))
        d = by_id(record_id)
        return run(d.tool, {d.id_arg: record_id}) if d else run("get_transaction", {"txn_id": record_id})
    return lookup


# ------------------------------------------------------------------ 2. audit log vs sessions

def reconcile_audit(audit_lines: Iterable[str], store, sessions: Iterable[str] | None = None,
                    *, tenant_id: str | None = None) -> Report:
    """Compare turn audit records with committed turns, per (session, msg_id).

    `tenant_id` is an operator-authorized tenant, not a value inferred from audit text.
    A scoped caller cannot switch tenants. Shared logs are filtered to that tenant; explicit
    session ids outside it are refused. Required for a tenant-scoped production store.

    committed_without_audit   a committed turn has no audit line: a gap in the trail (problem)
    uncommitted_outage        audit line for a turn that ended "unavailable" (expected: resent later)
    uncommitted_cancelled     the turn was cancelled (expected)
    uncommitted               audit line, no commit: a crash or lost lease after the audit write;
                              the trail shows work that did not count (problem to look at)
    rerun                     several audit lines for one committed turn: a re-run after a crash
    """
    scoped = current_tenant()
    if tenant_id is not None and (not tenant_id or (scoped is not None and scoped != tenant_id)):
        raise TenantViolation("reconciliation cannot change the caller's tenant")
    tenant_id = tenant_id or scoped
    rep = Report("audit")
    seen: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for line in audit_lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("stream") == "turn" and rec.get("msg_id"):
            if tenant_id is not None and session_tenant(rec["session"]) != tenant_id:
                continue  # a shared audit stream is filtered, never used to choose credentials
            seen[(rec["session"], rec["msg_id"])].append(rec)
    ids = set(sessions) if sessions is not None else {s for s, _ in seen}
    for sid in sorted(ids):
        if tenant_id is not None and session_tenant(sid) != tenant_id:
            raise TenantViolation("session is outside the reconciliation tenant")
        try:
            with tenant_scope(tenant_id) if tenant_id is not None else nullcontext():
                turns = store.turns(sid)
        except KeyError:
            turns = []
        committed = {t["msg_id"] for t in turns if t["status"] == "committed"}
        for msg in sorted(committed):
            rep.checked += 1
            lines = seen.get((sid, msg), [])
            if not lines:
                rep.add(Finding(f"{sid}/{msg}", "turn", "committed_without_audit"))
            elif len(lines) > 1:
                rep.add(Finding(f"{sid}/{msg}", "turn", "rerun", f"{len(lines)} audit lines"))
            else:
                rep.add(Finding(f"{sid}/{msg}", "turn", "confirmed"))
        for (s, msg), lines in seen.items():
            if s != sid or msg in committed:
                continue
            rep.checked += 1
            last = lines[-1].get("blocked_by")
            verdict = {"unavailable": "uncommitted_outage", "cancelled": "uncommitted_cancelled"}.get(
                last or "", "uncommitted")
            rep.add(Finding(f"{s}/{msg}", "turn", verdict, f"{len(lines)} audit line(s)"))
    return rep


# ------------------------------------------------------------------ 3. help-page index vs source

def reconcile_index(source, store, screen: Callable[[dict], str | None] | None = None) -> Report:
    """missing: a clean source page is not indexed · stale: indexed at another version ·
    orphaned: indexed, but gone from the source (an incremental sync never deletes) ·
    quarantined_but_indexed: the source page now fails screening yet is still searchable."""
    rep = Report("index")
    index = store.doc_index()
    src = {str(d["page_id"]): d for d in source.documents()}
    for pid, doc in sorted(src.items()):
        rep.checked += 1
        bad = screen(doc) if screen else None
        have = index.get(pid)
        if bad:
            rep.add(Finding(pid, "page", "quarantined_but_indexed" if have else "confirmed", bad))
        elif have is None:
            rep.add(Finding(pid, "page", "missing"))
        elif str(have.get("version")) != str(doc["version"]):
            rep.add(Finding(pid, "page", "stale", f"index v{have.get('version')}, source v{doc['version']}"))
        else:
            rep.add(Finding(pid, "page", "confirmed"))
    for pid in sorted(set(index) - set(src)):
        rep.checked += 1
        rep.add(Finding(pid, "page", "orphaned", "deleted at the source; run a full sync"))
    return rep


def summary(reports: list[Report]) -> dict:
    out: dict = {r.job: r.as_dict() for r in reports}
    out["problems"] = sum(len(r.problems) for r in reports)
    return out
