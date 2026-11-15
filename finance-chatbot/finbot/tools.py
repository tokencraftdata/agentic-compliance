"""Typed, read-only tools with tenant scoping enforced in code.

Key rules (each fixes a failure mode in the original design):
  * The tenant (merchant / customer id) comes from the authenticated session,
    never from the model's tool arguments. The model cannot ask for another
    tenant's data even if a prompt injection tells it to.
  * Every tool returns an envelope: {status: found|not_found|error, source, data}.
    "not_found" is a first-class answer that the evidence guard enforces later.
  * Backends sit behind small adapter classes (DB, log search, KB). In production
    these are your MCP servers / services; credentials are short-lived (IAM role,
    STS, Secrets Manager rotation), never shared static keys.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

TXN_ID = re.compile(r"\b(txn_[A-Za-z0-9]{6,})\b")
HEALTH_WINDOW = 500   # processor-health stats use the tenant's newest N transactions


def envelope(status: str, source: str, data: Any = None, note: str = "") -> dict:
    out = {"status": status, "source": source, "data": data}
    if note:
        out["note"] = note
    return out


class TransactionStore(Protocol):
    def get(self, tenant_id: str, txn_id: str) -> dict | None: ...

    def recent(self, tenant_id: str, limit: int) -> list[dict]:
        """The tenant's newest `limit` records (bounded: a large merchant has millions)."""
        ...


class LogSearch(Protocol):
    def events(self, tenant_id: str, txn_id: str, limit: int) -> list[dict]: ...


@dataclass
class Session:
    session_id: str
    tenant_id: str          # from auth token, not from chat
    user_groups: list[str]  # for KB access filtering
    role: str = "support"   # support | merchant


class ToolBox:
    """Registry: JSON schemas for the LLM + Python handlers bound to a session."""

    def __init__(self, txns: TransactionStore, logs: LogSearch, kb, sleep=None, processors=None):
        import time

        from .resilience import CircuitBreaker
        self.txns, self.logs, self.kb = txns, logs, kb
        self.processors = processors   # ProcessorDirectory, or None when routing tools are not offered
        self.sleep = sleep or time.sleep
        self.breakers = {n: CircuitBreaker(n) for n in ("txn_db", "log_search", "knowledge_base")}

    def _call(self, backend: str, fn):
        """Read-only backend call with backoff on transient errors (safe to repeat)."""
        from .resilience import retry_call
        return retry_call(fn, attempts=3, base_s=0.2, breaker=self.breakers[backend], sleep=self.sleep)

    # ---- schemas (strict: the model cannot add extra fields such as tenant_id)
    def schemas(self) -> list[dict]:
        return [
            {
                "name": "get_transaction",
                "description": ("Fetch the authoritative record for one transaction belonging to the "
                                "current customer: status, amount, currency, decline code, processor "
                                "response, timestamps. Use whenever the user asks about a specific "
                                "transaction. Returns status=not_found if it does not exist for this customer."),
                "input_schema": {"type": "object", "additionalProperties": False,
                                 "properties": {"txn_id": {"type": "string", "pattern": "^txn_[A-Za-z0-9]{6,}$"}},
                                 "required": ["txn_id"]},
                "strict": True,
            },
            {
                "name": "search_transaction_logs",
                "description": ("Fetch gateway/processor log events for one transaction (routing, retries, "
                                "3DS, fraud checks, processor errors), newest first. Use to explain WHY a "
                                "transaction failed after get_transaction."),
                "input_schema": {"type": "object", "additionalProperties": False,
                                 "properties": {"txn_id": {"type": "string", "pattern": "^txn_[A-Za-z0-9]{6,}$"},
                                                "limit": {"type": "integer"}},
                                 "required": ["txn_id", "limit"]},
                "strict": True,
            },
            {
                "name": "search_knowledge_base",
                "description": ("Search internal documentation (decline codes, API specs, surcharge rules, "
                                "runbooks). Use for 'what does X mean / how does Y work' questions. Returns "
                                "chunks with source URLs; cite them."),
                "input_schema": {"type": "object", "additionalProperties": False,
                                 "properties": {"query": {"type": "string"}},
                                 "required": ["query"]},
                "strict": True,
            },
        ] + self._routing_schemas()

    def _routing_schemas(self) -> list[dict]:
        if self.processors is None:
            return []
        txn = {"type": "string", "pattern": "^txn_[A-Za-z0-9]{6,}$"}
        return [
            {
                "name": "get_routing_trace",
                "description": ("Fetch how one transaction was routed across payment processors: candidate "
                                "processors, the routing rules applied, every processor attempt with its "
                                "normalised response code, which attempts cascaded and why, and cascade_advice "
                                "(could another processor have helped). Use for questions about processors, "
                                "routing, cascades, failover or 'should I send it to another processor'."),
                "input_schema": {"type": "object", "additionalProperties": False,
                                 "properties": {"txn_id": txn}, "required": ["txn_id"]},
                "strict": True,
            },
            {
                "name": "get_processor_health",
                "description": ("Fetch a payment processor's known incidents (outages, degradation) and the "
                                "current customer's own attempt and error counts on it. Use before saying a "
                                "processor is or was down."),
                "input_schema": {"type": "object", "additionalProperties": False,
                                 "properties": {"processor": {"type": "string",
                                                              "enum": sorted(self.processors.names())}},
                                 "required": ["processor"]},
                "strict": True,
            },
        ]

    def handler(self, session: Session) -> Callable[[str, dict], dict]:
        from .policy import guidance
        from .resilience import ServiceUnavailable

        def run(name: str, args: dict) -> dict:
            try:
                if name == "get_transaction":
                    rec = self._record(session, args["txn_id"])
                    if rec is None:
                        return envelope("not_found", "txn_db",
                                        note=f"No transaction {args['txn_id']} exists for this customer.")
                    data = {k: v for k, v in rec.items() if k != "tenant_id"}
                    g = guidance(rec, session.role)
                    if g:  # authoritative retry rule + next step for THIS user's role
                        data["guidance"] = g
                    return envelope("found", "txn_db", data)
                if name == "search_transaction_logs":
                    limit = max(1, min(int(args.get("limit", 20)), 50))
                    ev = self._call("log_search", lambda: self.logs.events(session.tenant_id, args["txn_id"], limit))
                    if not ev:
                        return envelope("not_found", "log_search", note="No log events found for this transaction.")
                    return envelope("found", "log_search", ev)
                if name == "search_knowledge_base":
                    hits = self._call("knowledge_base",
                                      lambda: self.kb.search(args["query"], k=4, groups=session.user_groups))
                    if not hits:
                        return envelope("not_found", "knowledge_base",
                                        note="No documentation matched. Do not answer from general knowledge.")
                    return envelope("found", "knowledge_base", hits)
                if name == "get_routing_trace" and self.processors is not None:
                    return self._routing_trace(session, args["txn_id"])
                if name == "get_processor_health" and self.processors is not None:
                    return self._processor_health(session, args["processor"])
                return envelope("error", name, note="unknown tool")
            except ServiceUnavailable:
                return envelope("error", name, note="temporarily unavailable after retries; tell the user to "
                                "try again in a few minutes and do not guess the answer")
            except Exception as e:  # never leak stack traces / connection strings to the model
                return envelope("error", name, note=f"backend unavailable ({type(e).__name__})")
        return run

    def _record(self, session: Session, txn_id: str) -> dict | None:
        """One transaction for this session's tenant, or None (missing or another tenant's)."""
        rec = self._call("txn_db", lambda: self.txns.get(session.tenant_id, txn_id))
        if rec is None or rec.get("tenant_id") != session.tenant_id:  # defence in depth
            return None
        return rec

    def _routing_trace(self, session: Session, txn_id: str) -> dict:
        from .processors.explain import routing_view
        rec = self._record(session, txn_id)
        if rec is None:
            return envelope("not_found", "routing", note=f"No transaction {txn_id} exists for this customer.")
        view = routing_view(rec)
        if view is None:
            return envelope("not_found", "routing", note="No routing trace is recorded for this transaction.")
        return envelope("found", "routing", view)

    def _processor_health(self, session: Session, processor: str) -> dict:
        from .processors.explain import health_view
        assert self.processors is not None
        records = self._call("txn_db", lambda: self.txns.recent(session.tenant_id, HEALTH_WINDOW))
        view = health_view(processor, self.processors, records)
        if view is None:
            return envelope("not_found", "processor_health", note=f"No processor named {processor!r}.")
        return envelope("found", "processor_health", view)


# ---------------------------------------------------------------- demo backends

class InMemoryTxns:
    def __init__(self, rows: list[dict]):
        self.rows = {r["txn_id"]: r for r in rows}

    def get(self, tenant_id, txn_id):
        r = self.rows.get(txn_id)
        return r if r and r["tenant_id"] == tenant_id else None

    def recent(self, tenant_id, limit):
        mine = [r for r in self.rows.values() if r["tenant_id"] == tenant_id]
        return sorted(mine, key=lambda r: r.get("created_at", ""), reverse=True)[:limit]


class InMemoryLogs:
    def __init__(self, events: list[dict]):
        self.events_ = events

    def events(self, tenant_id, txn_id, limit):
        return [e for e in self.events_ if e["txn_id"] == txn_id and e["tenant_id"] == tenant_id][:limit]


DEMO_TXNS = [
    {"txn_id": "txn_A1B2C3D4", "tenant_id": "m_100", "status": "declined", "amount": "129.00",
     "currency": "USD", "decline_code": "05", "processor_response": "Do not honor",
     "created_at": "2026-11-12T14:03:11Z", "card_brand": "visa", "last4": "4242"},
    {"txn_id": "txn_E5F6G7H8", "tenant_id": "m_100", "status": "approved", "amount": "54.20",
     "currency": "USD", "surcharge": "1.62", "created_at": "2026-11-12T15:40:02Z",
     "card_brand": "mastercard", "last4": "4444"},
    {"txn_id": "txn_OTHER999", "tenant_id": "m_200", "status": "approved", "amount": "9999.00",
     "currency": "USD", "created_at": "2026-11-12T16:00:00Z"},
]
DEMO_LOGS = [
    {"txn_id": "txn_A1B2C3D4", "tenant_id": "m_100", "ts": "2026-11-12T14:03:10Z",
     "event": "fraud_check", "result": "pass", "score": 12},
    {"txn_id": "txn_A1B2C3D4", "tenant_id": "m_100", "ts": "2026-11-12T14:03:11Z",
     "event": "issuer_response", "code": "05", "message": "Do not honor"},
]
DEMO_DOCS = [
    {"page_id": "101", "title": "Decline codes", "version": 3, "updated": "2026-10-01",
     "url": "https://wiki.example.com/decline-codes", "space": "PAY",
     "html": "<h2>Code 05 Do not honor</h2><p>The issuing bank declined without a specific reason. "
             "The merchant cannot fix this; the cardholder must contact their bank or use another card. "
             "Do not retry more than once in 24 hours.</p><h2>Code 51 Insufficient funds</h2>"
             "<p>The card has insufficient available balance.</p>"},
    {"page_id": "102", "title": "Surcharging", "version": 5, "updated": "2026-09-15",
     "url": "https://wiki.example.com/surcharge", "space": "PAY",
     "html": "<h2>Surcharge request</h2><p>Send surcharge_amount in minor units with the authorization "
             "request. Surcharges apply to credit cards only, are capped at 3 percent, and must be "
             "disclosed before payment. Debit and prepaid cards cannot be surcharged.</p>"},
]
