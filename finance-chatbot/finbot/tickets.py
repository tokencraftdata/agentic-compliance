"""Tickets: how a write request reaches a person. The bot creates; only people resolve.

    Ticket        what was asked, by which tenant and role, about which transaction,
                  with the verified facts and retry policy at the time (card data masked)
    TicketStore   port: InMemoryTickets (tests, one process), JsonFileTickets (one host),
                  DynamoTickets (every worker and the ticket desk share it; same table as
                  the session store). Or your ticketing system (Jira, ServiceNow,
                  Zendesk) behind the same four methods.
    TicketDesk    the human side: list, claim, resolve. Support or admin only, audited.

Ticket ids are derived from (tenant, session, kind, transaction), so asking twice
in a session does not open two tickets, and a crashed turn that is re-run finds
the ticket it already created.

Nothing in finbot executes a ticket. A person does the change in the real system
(payment console, back office) and records the outcome here.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Protocol

from . import auditlog
from .pii import mask_pii, mask_value
from .security import Authorizer, Forbidden, Principal

OPEN, CLAIMED, DONE, REJECTED = "open", "claimed", "done", "rejected"


@dataclass(frozen=True)
class Ticket:
    id: str
    tenant_id: str
    session_id: str
    requested_by_role: str
    kind: str
    txn_id: str | None
    request_text: str                       # masked
    facts: dict[str, Any] = field(default_factory=dict)   # verified record at request time
    policy_note: str = ""
    status: str = OPEN
    created: float = 0.0
    assignee: str = ""
    outcome_note: str = ""
    resolved: float = 0.0


def ticket_id(tenant_id: str, session_id: str, kind: str, txn_id: str | None) -> str:
    h = hashlib.sha256(f"{tenant_id}|{session_id}|{kind}|{txn_id or '-'}".encode()).hexdigest()
    return f"TCK-{h[:8].upper()}"


class TicketStore(Protocol):
    def create_if_absent(self, t: Ticket) -> tuple[Ticket, bool]: ...

    def get(self, ticket_id: str) -> Ticket | None: ...

    def put(self, t: Ticket) -> None: ...

    def all(self) -> list[Ticket]: ...


class InMemoryTickets:
    def __init__(self) -> None:
        self.items: dict[str, Ticket] = {}

    def create_if_absent(self, t: Ticket) -> tuple[Ticket, bool]:
        if t.id in self.items:
            return self.items[t.id], False
        self.items[t.id] = t
        return t, True

    def get(self, ticket_id: str) -> Ticket | None:
        return self.items.get(ticket_id)

    def put(self, t: Ticket) -> None:
        self.items[t.id] = t

    def all(self) -> list[Ticket]:
        return list(self.items.values())


class JsonFileTickets(InMemoryTickets):
    """One JSON file, rewritten atomically. Single host only."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        if os.path.exists(path):
            with open(path) as f:
                self.items = {d["id"]: Ticket(**d) for d in json.load(f)}

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump([asdict(t) for t in self.items.values()], f, indent=1)
        os.replace(tmp, self.path)

    def create_if_absent(self, t: Ticket) -> tuple[Ticket, bool]:
        out = super().create_if_absent(t)
        if out[1]:
            self._save()
        return out

    def put(self, t: Ticket) -> None:
        super().put(t)
        self._save()


def open_ticket(store: TicketStore, *, tenant_id: str, session_id: str, role: str, kind: str,
                txn_id: str | None, text: str, facts: dict[str, Any], policy_note: str = "",
                clock: Callable[[], float] = time.time) -> tuple[Ticket, bool]:
    t = Ticket(ticket_id(tenant_id, session_id, kind, txn_id), tenant_id, session_id, role, kind, txn_id,
               mask_pii(text)[0], mask_value(facts), policy_note,
               OPEN, clock())
    return store.create_if_absent(t)


class TicketDesk:
    """For people. The chatbot never calls these methods."""

    def __init__(self, store: TicketStore, authorizer: Authorizer | None = None, audit_path: str | None = None,
                 clock: Callable[[], float] = time.time):
        self.store, self.authz, self.audit_path, self.clock = store, authorizer or Authorizer(), audit_path, clock

    def list(self, who: Principal, status: str = OPEN) -> list[Ticket]:
        self.authz.can_review(who)
        return sorted((t for t in self.store.all() if t.status == status), key=lambda t: t.created)

    def get(self, who: Principal, ticket_id: str) -> Ticket:
        t = self.store.get(ticket_id)
        if t is None or (who.role not in ("support", "admin") and t.tenant_id != who.tenant_id):
            raise Forbidden("ticket not found or not yours")
        if who.role not in ("customer", "merchant", "support", "admin"):
            raise Forbidden("role may not read tickets")
        return t

    def claim(self, who: Principal, ticket_id: str) -> Ticket:
        self.authz.can_review(who)
        t = self._get(ticket_id)
        if t.status != OPEN:
            raise ValueError(f"{ticket_id} is {t.status}, not open")
        return self._save(who, replace(t, status=CLAIMED, assignee=who.subject), "claim")

    def resolve(self, who: Principal, ticket_id: str, outcome: str, note: str) -> Ticket:
        """Record what the person did in the real system. `note` is required."""
        self.authz.can_review(who)
        if outcome not in (DONE, REJECTED):
            raise ValueError("outcome must be 'done' or 'rejected'")
        if not note.strip():
            raise ValueError("resolving a ticket needs a note saying what was done")
        t = self._get(ticket_id)
        if t.status not in (OPEN, CLAIMED):
            raise ValueError(f"{ticket_id} is already {t.status}")
        return self._save(who, replace(t, status=outcome, assignee=t.assignee or who.subject,
                                       outcome_note=mask_pii(note)[0], resolved=self.clock()), outcome)

    def _get(self, ticket_id: str) -> Ticket:
        t = self.store.get(ticket_id)
        if t is None:
            raise KeyError(ticket_id)
        return t

    def _save(self, who: Principal, t: Ticket, op: str) -> Ticket:
        self.store.put(t)
        auditlog.write(self.audit_path, "tickets", {"ts": self.clock(), "actor": who.subject, "op": op,
                                                     "ticket": t.id, "status": t.status})
        return t


class DynamoTickets:
    """Tickets in the session store's DynamoDB table, so every worker pod and the people at
    the desk see the same tickets. Items: pk "TICKETS", sk "K#<id>". create_if_absent is a
    conditional put, so two workers racing on the same request create one ticket.
    One partition is fine for a support desk's volume; shard pk by tenant beyond that."""

    PK = "TICKETS"

    def __init__(self, table: Any):
        self.t = table

    @staticmethod
    def _item(t: Ticket) -> dict[str, Any]:
        return {"pk": DynamoTickets.PK, "sk": f"K#{t.id}", "ticket": json.dumps(asdict(t))}

    def create_if_absent(self, t: Ticket) -> tuple[Ticket, bool]:
        from .store_dynamo import ConditionFailed
        try:
            self.t.put(self._item(t), ("attribute_not_exists(pk)", {}, {}))
            return t, True
        except ConditionFailed:
            existing = self.get(t.id)
            assert existing is not None
            return existing, False

    def get(self, ticket_id: str) -> Ticket | None:
        r = self.t.get(self.PK, f"K#{ticket_id}")
        return Ticket(**json.loads(r["ticket"])) if r else None

    def put(self, t: Ticket) -> None:
        self.t.put(self._item(t))

    def all(self) -> list[Ticket]:
        return [Ticket(**json.loads(r["ticket"])) for r in self.t.query(self.PK, "K#")]
