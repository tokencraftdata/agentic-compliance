"""Entity-scoped session memory (applies the MemoryKeeper findings).

Problem in the original bot: one long chat transcript is the only memory, so
facts about transaction A leak into an answer about transaction B or a generic
question, and long chats drift or break.

Design:
  * A typed ledger keyed by entity (transaction id). Facts are recorded only from
    tool results with source + time, never from model prose (summary-only
    memory revived stale facts in 33/72 checkpoints in the paper).
  * An explicit focus: the entity the current question is about. Switching
    focus is detected in code (a new txn id or a "general" question) and
    announced to the model through a mid-conversation system message, so
    earlier entities are marked "do not reuse unless the user refers back".
  * Corrections supersede facts (they don't delete them), keeping an audit trail.
  * State is re-rendered from the ledger each turn; the LLM never holds it.
  * Long threads roll over: after N turns a fresh thread starts, seeded with the
    rendered ledger, not with a lossy summary.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .tools import TXN_ID

GENERAL_CUES = ("in general", "generally", "how does", "what is", "what does", "explain", "what are")


@dataclass
class Fact:
    key: str
    value: object
    source: str
    at: float
    superseded_by: str | None = None


@dataclass
class EntityState:
    entity_id: str
    facts: dict[str, list[Fact]] = field(default_factory=dict)
    not_found: bool = False

    def current(self) -> dict:
        return {k: v[-1].value for k, v in self.facts.items() if v and v[-1].superseded_by is None}

    def record(self, key: str, value, source: str) -> None:
        hist = self.facts.setdefault(key, [])
        if hist and hist[-1].value == value:
            return
        if hist:
            hist[-1].superseded_by = source
        hist.append(Fact(key, value, source, time.time()))


@dataclass
class SessionMemory:
    entities: dict[str, EntityState] = field(default_factory=dict)
    focus: str | None = None
    turns_in_thread: int = 0
    consecutive_fallbacks: int = 0  # unverifiable answers in a row -> offer a human
    risk: float = 0.0  # session risk score (finbot/adaptive/risk.py); at the policy threshold -> a person

    def update_focus(self, user_text: str) -> tuple[str | None, bool]:
        """Return (new_focus, changed). Decided in code, not by the model."""
        ids = TXN_ID.findall(user_text)
        prev = self.focus
        if ids:
            self.focus = ids[-1]
        elif any(c in user_text.lower() for c in GENERAL_CUES) and not any(
                w in user_text.lower() for w in ("this transaction", "that transaction", "it fail", "this payment")):
            self.focus = None  # generic question: detach from any transaction
        return self.focus, self.focus != prev

    def ingest_tool_result(self, name: str, args: dict, result: dict) -> None:
        tid = args.get("txn_id")
        if not tid:
            return
        ent = self.entities.setdefault(tid, EntityState(tid))
        if result["status"] == "not_found" and name == "get_transaction":
            ent.not_found = True
            return
        if result["status"] != "found":
            return
        ent.not_found = False
        if name == "get_transaction":
            for k, v in result["data"].items():
                ent.record(k, v, f"{result['source']}:{name}")
        elif name == "search_transaction_logs":
            ent.record("log_events", result["data"], f"{result['source']}:{name}")
        elif name == "get_routing_trace":
            ent.record("routing", result["data"], f"{result['source']}:{name}")

    def render(self) -> str:
        """Typed state for the model. Only the focus entity is 'active'."""
        lines = ["<session_state>"]
        if self.focus:
            ent = self.entities.get(self.focus)
            lines.append(f"focus_transaction: {self.focus}")
            if ent and ent.not_found:
                lines.append("focus_status: NOT FOUND for this customer (verified by lookup)")
            elif ent:
                lines.append(f"verified_facts: {ent.current()}")
            else:
                lines.append("verified_facts: none yet; call tools before stating any fact")
        else:
            lines.append("focus_transaction: none (general question; do not apply any "
                         "earlier transaction's details unless the user explicitly refers back)")
        others = [e for e in self.entities if e != self.focus]
        if others:
            lines.append(f"other_transactions_in_session (inactive, do not mix in): {others}")
        lines.append("</session_state>")
        return "\n".join(lines)
