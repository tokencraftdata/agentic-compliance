"""Domain plug-ins: one per kind of record the bot can read.

The core (turn pipeline, hallucination gate, session memory, tickets, crash recovery) knows
nothing about bank accounts, transfers, identity checks or loans. A Domain tells it:

    prefix, tool, id_arg   how a record is named and fetched         acct_...  get_account(account_id)
    fields                 the only fields the model may see (anything else is dropped)
    statuses               the words an answer may use for the record's status
    policy                 reason key -> what may be said and done about it (a table, not a prompt)
    summary                the record stated by code, with every field validated
    causes, cause_phrases  reason key -> cause, and the plain words people use for each cause
    implied                statuses a record has passed through (a paid claim was approved)
    status_causes          a cause the status itself carries (a delinquent mortgage is delinquent)

Adding an area is one module that builds a Domain and calls register(). Which domains a
deployment offers is a setting (FINBOT_DOMAINS); nothing else changes.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

PAYMENTS = "payments"            # the card-payments plug-in (finbot/tools.py, finbot/processors)
_PREFIXES = ["txn"]              # id prefixes the core recognises; register() adds one per domain
_ID_ARGS = ["txn_id"]


@dataclass(frozen=True)
class Rule:
    """What policy says about one reason key. `resend` is None when sending again is not a question."""
    meaning: str                       # plain words, safe to show
    customer: str                      # next step for the customer
    support: str                       # next step for support staff
    resend: bool | None = None         # may the same request be sent again?
    resend_rule: str = ""              # the rule, with its limits
    only_if: tuple[str, ...] = ()      # when resend is False: wording that makes "send again" acceptable


@dataclass(frozen=True)
class Domain:
    name: str                          # "transfers"
    noun: str                          # "transfer"
    prefix: str                        # "xfer"
    tool: str                          # "get_transfer"
    id_arg: str                        # "transfer_id"
    source: str                        # "transfer_db"
    description: str                   # tool description shown to the model
    fields: frozenset[str]             # allow-list: other fields never reach the model
    statuses: dict[str, str]           # status word in an answer -> the record's status value
    open_statuses: frozenset[str]      # outcome not decided yet
    policy: dict[str, Rule]
    reason_key: Callable[[dict], str]  # the record's reason key, or ""
    summary: Callable[[dict], str | None]
    causes: dict[str, str]
    cause_phrases: dict[str, str] = field(default_factory=dict)
    implied: dict[str, frozenset[str]] = field(default_factory=dict)
    status_causes: dict[str, str] = field(default_factory=dict)

    def guidance(self, record: dict, role: str) -> dict | None:
        """The policy next step for this record and this reader, or None when nothing is wrong."""
        key = self.reason_key(record)
        rule = self.policy.get(key)
        if rule is None:
            return None
        out: dict = {"reason_key": key, "meaning": rule.meaning,
                     "next_step": rule.support if role in ("support", "admin") else rule.customer}
        if rule.resend is not None:
            out.update(retryable=rule.resend, retry_rule=rule.resend_rule)
            if not rule.resend:
                out["retry_only_if"] = list(rule.only_if)
        return out

    def pattern(self) -> str:
        return rf"^{self.prefix}_[A-Za-z0-9]{{6,}}$"

    def schema(self) -> dict:
        return {"name": self.tool, "description": self.description,
                "input_schema": {"type": "object", "additionalProperties": False,
                                 "properties": {self.id_arg: {"type": "string", "pattern": self.pattern()}},
                                 "required": [self.id_arg]},
                "strict": True}


DOMAINS: dict[str, Domain] = {}


def register(domain: Domain) -> Domain:
    DOMAINS[domain.name] = domain
    if domain.prefix not in _PREFIXES:
        _PREFIXES.append(domain.prefix)
        _ID_ARGS.append(domain.id_arg)
    return domain


def find_ids(text: str) -> list[str]:
    """Every record id in the text, in order (payments ids and every registered domain's)."""
    return re.findall(rf"\b((?:{'|'.join(_PREFIXES)})_[A-Za-z0-9]{{6,}})\b", text)


def id_of(args: dict) -> str | None:
    """The record id a tool call names, whichever argument carries it."""
    for k in _ID_ARGS:
        if isinstance(args.get(k), str):
            return args[k]
    return None


def by_id(record_id: str | None) -> Domain | None:
    prefix = (record_id or "").split("_", 1)[0]
    return next((d for d in DOMAINS.values() if d.prefix == prefix), None)


def by_tool(tool: str) -> Domain | None:
    return next((d for d in DOMAINS.values() if d.tool == tool), None)


def record_tools() -> set[str]:
    """Tools that fetch one authoritative record."""
    return {"get_transaction"} | {d.tool for d in DOMAINS.values()}


def enabled(names: str) -> list[str]:
    """Parse FINBOT_DOMAINS ("accounts,transfers,kyc,lending,payments"); unknown names are an error."""
    want = [n.strip() for n in names.split(",") if n.strip()]
    unknown = [n for n in want if n != PAYMENTS and n not in DOMAINS]
    if unknown:
        raise ValueError(f"unknown domain(s) {unknown}; known: {sorted([*DOMAINS, PAYMENTS])}")
    return want


# ---- validated pieces for summaries (a summary never repeats a field it could not validate)

AMOUNT = re.compile(r"^\d+\.\d{2}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,31}$")
WORD = re.compile(r"^[a-z][a-z_]{1,31}$")
RATE = re.compile(r"^\d{1,2}\.\d{2}%$")


def money(d: dict, key: str) -> str | None:
    amount, currency = str(d.get(key, "")), str(d.get("currency", ""))
    return f"{amount} {currency}" if AMOUNT.match(amount) and CURRENCY.match(currency) else None


def ok(pattern: re.Pattern, value) -> str | None:
    return value if isinstance(value, str) and pattern.match(value) else None
