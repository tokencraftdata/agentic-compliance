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


class ToolRunner:
    """Runs one tool call for a turn, after checking the turn's delegation grant. A remote
    tool adapter forwards `grant` so the data owner can check it again."""

    def __init__(self, run: Callable[[str, dict], dict], grants, session: Session, grant: str):
        self._run, self.grants, self.session, self.grant = run, grants, session, grant

    def __call__(self, name: str, args: dict) -> dict:
        from .agent_identity import GrantDenied
        try:
            self.grants.verify(self.grant, tool=name, tenant_id=self.session.tenant_id,
                               session_id=self.session.session_id)
        except GrantDenied as e:
            return envelope("refused", "authz", note=f"Not permitted for this user: {e}.")
        return self._run(name, args)


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
    role: str = "support"   # support | customer (or the legacy alias "merchant")
    subject: str = ""       # the user, from the token (named in the agent's delegation grant)
    scopes: list[str] | None = None   # token scopes; None = the role's defaults (agent_identity.py)


class RecordStore(Protocol):
    def get(self, domain: str, tenant_id: str, record_id: str) -> dict | None:
        """One record of a domain plug-in (finbot/domains) for this tenant, or None."""
        ...


class ToolBox:
    """Registry: JSON schemas for the LLM + Python handlers bound to a session.

    `domains` lists the plug-ins this deployment offers (finbot/domains): "payments" turns
    on the card-payment tools, every other name adds that domain's record tool."""

    def __init__(self, txns: TransactionStore, logs: LogSearch, kb, sleep=None, processors=None,
                 records: RecordStore | None = None, domains: list[str] | None = None, grants=None):
        import time

        from .agent_identity import GrantIssuer
        from .domains import DOMAINS, PAYMENTS
        from .resilience import CircuitBreaker
        self.txns, self.logs, self.kb = txns, logs, kb
        self.records = records
        names = domains if domains is not None else [PAYMENTS, *(DOMAINS if records is not None else [])]
        self.payments = PAYMENTS in names
        self.domains = [DOMAINS[n] for n in names if n != PAYMENTS] if records is not None else []
        self.processors = processors if self.payments else None   # None: routing tools are not offered
        self.sleep = sleep or time.sleep
        self.grants = grants or GrantIssuer()   # per-turn delegation grants (finbot/agent_identity.py)
        self.breakers = {n: CircuitBreaker(n) for n in ("txn_db", "log_search", "knowledge_base", "records")}

    def _call(self, backend: str, fn):
        """Read-only backend call with backoff on transient errors (safe to repeat)."""
        from .resilience import retry_call
        return retry_call(fn, attempts=3, base_s=0.2, breaker=self.breakers[backend], sleep=self.sleep)

    # ---- schemas (strict: the model cannot add extra fields such as tenant_id)
    def schemas(self) -> list[dict]:
        kb = {
            "name": "search_knowledge_base",
            "description": ("Search internal documentation (product rules, codes, fees, limits, runbooks). Use "
                            "for 'what does X mean / how does Y work' questions. Returns chunks with source "
                            "URLs; cite them."),
            "input_schema": {"type": "object", "additionalProperties": False,
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]},
            "strict": True,
        }
        return [*(self._payment_schemas() if self.payments else []), kb, *(d.schema() for d in self.domains)]

    def _payment_schemas(self) -> list[dict]:
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

    def handler(self, session: Session) -> ToolRunner:
        """The turn's tool runner. It carries a delegation grant for this user, tenant and
        session; every call is checked against it before any backend is touched."""
        from .agent_identity import effective_scopes
        grant = self.grants.mint(session.subject, session.tenant_id, session.session_id,
                                 effective_scopes(session.role, session.scopes))
        return ToolRunner(self._runner(session), self.grants, session, grant)

    def _runner(self, session: Session) -> Callable[[str, dict], dict]:
        from .domains import by_tool
        from .policy import guidance
        from .resilience import ServiceUnavailable

        def run(name: str, args: dict) -> dict:
            try:
                domain = by_tool(name)
                if domain is not None and domain in self.domains:
                    return self._domain_record(session, domain, str(args.get(domain.id_arg, "")))
                if name in ("get_transaction", "search_transaction_logs") and not self.payments:
                    return envelope("error", name, note="unknown tool")
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

    def _domain_record(self, session: Session, domain, record_id: str) -> dict:
        """One record of a domain plug-in: tenant-scoped, cut to the domain's allow-list of
        fields (so a field the model must not see never leaves this function), with the
        domain's policy guidance attached."""
        assert self.records is not None
        store = self.records
        rec = self._call("records", lambda: store.get(domain.name, session.tenant_id, record_id))
        if rec is None or rec.get("tenant_id") != session.tenant_id:
            return envelope("not_found", domain.source,
                            note=f"No {domain.noun} {record_id} exists for this customer.")
        data = {k: v for k, v in rec.items() if k in domain.fields}
        g = domain.guidance(rec, session.role)
        if g:
            data["guidance"] = g
        return envelope("found", domain.source, data)

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


class InMemoryRecords:
    """Domain records by domain name, e.g. {"accounts": [...], "transfers": [...]}."""

    def __init__(self, rows: dict[str, list[dict]]):
        from .domains import DOMAINS
        self.rows = {name: {r[DOMAINS[name].id_arg]: r for r in items} for name, items in rows.items()
                     if name in DOMAINS}

    def get(self, domain, tenant_id, record_id):
        r = self.rows.get(domain, {}).get(record_id)
        return r if r and r["tenant_id"] == tenant_id else None


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
DEMO_RECORDS: dict[str, list[dict]] = {
    "accounts": [
        {"account_id": "acct_M4N5P6Q7", "tenant_id": "m_100", "type": "checking", "status": "active",
         "currency": "USD", "balance": "2450.00", "available_balance": "1950.00", "opened_on": "2025-03-04",
         "holds": [{"amount": "500.00", "reason_key": "CHECK_DEPOSIT", "release_date": "2026-11-18"}],
         "fees": [{"type": "monthly_maintenance", "amount": "12.00", "posted_on": "2026-11-01"}]},
    ],
    "transfers": [
        {"transfer_id": "xfer_R8S9T0U1", "tenant_id": "m_100", "rail": "ach", "direction": "outgoing",
         "status": "returned", "amount": "640.00", "currency": "USD", "return_code": "R02",
         "created_at": "2026-11-10T09:15:00Z", "to_account_last4": "7781"},
    ],
    "kyc": [
        {"case_id": "kyc_V2W3X4Y5", "tenant_id": "m_100", "status": "in_review", "level": "enhanced",
         "submitted_on": "2026-11-09", "review_reason": "manual_screening_queue"},
    ],
    "disputes": [
        {"dispute_id": "dsp_D1E2F3G4", "tenant_id": "m_100", "status": "needs_response",
         "reason_code": "PRODUCT_NOT_RECEIVED", "amount": "89.00", "currency": "USD", "respond_by": "2026-11-24",
         "opened_on": "2026-11-10", "evidence_submitted": False},
    ],
    "mortgages": [
        {"mortgage_id": "mtg_H5J6K7L8", "tenant_id": "m_100", "product": "fixed_rate", "status": "delinquent",
         "currency": "USD", "principal_balance": "312450.00", "rate": "6.25%", "next_payment_amount": "2140.00",
         "next_payment_due": "2026-11-01", "days_past_due": 14},
    ],
    "bnpl": [
        {"plan_id": "bnpl_M9N8P7Q6", "tenant_id": "m_100", "status": "active", "currency": "USD",
         "purchase_amount": "480.00", "installments_total": 4, "installments_paid": 2,
         "next_installment_amount": "120.00", "next_installment_due": "2026-11-28",
         "last_payment": {"result": "declined", "next_attempt_on": "2026-11-18"}},
    ],
    "insurance": [
        {"claim_id": "clm_R5S4T3U2", "tenant_id": "m_100", "policy_type": "business_property", "status": "in_review",
         "currency": "USD", "claimed_amount": "18500.00", "submitted_on": "2026-11-03",
         "adjuster_notes": "possible pre-existing damage", "fraud_score": 71},
    ],
    "subscriptions": [
        {"subscription_id": "sub_V1W2X3Y4", "tenant_id": "m_100", "plan": "pro", "status": "active",
         "currency": "USD", "price": "49.00", "interval": "month", "current_period_end": "2026-11-30",
         "cancel_at_period_end": True},
    ],
    "lending": [
        {"loan_id": "loan_Z6A7B8C9", "tenant_id": "m_100", "product": "term_loan", "status": "declined",
         "principal": "25000.00", "currency": "USD", "decided_on": "2026-11-06",
         "decision_reasons": ["DTI_HIGH", "CREDIT_HISTORY_SHORT"], "credit_score": 612},
    ],
}
