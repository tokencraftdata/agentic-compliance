"""Stateless workers on top of the durable session store.

Any worker can serve any session. Per message:

  claim lease -> load committed state -> journal turn start (dedupe by msg_id)
  -> run the turn in memory -> commit (compare-and-swap on version + lease)

If a worker dies mid-turn, nothing is committed. After its lease expires
another worker claims the session, sees the journalled-but-uncommitted turn
and re-runs it from the last committed state. If the original worker comes
back late, its commit fails with StaleWrite and its answer is discarded.

What a replacement inherits is only the committed case record: tool-derived
facts, focus, notices and history. It never inherits the dead worker's
in-flight reasoning, and text inside documents or earlier model output gains
no authority from the handoff.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict

from .memory import EntityState, Fact, SessionMemory
from .orchestrator import Chatbot, ChatSession
from .store import SessionStore
from .tools import Session

# ---------------------------------------------------------------- (de)serialisation

def _jsonable(x):
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if hasattr(x, "model_dump"):  # Anthropic SDK content blocks (incl. thinking signatures)
        return x.model_dump(exclude_none=True)
    if hasattr(x, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(x).items()}
    return str(x)


def dump_session(cs: ChatSession) -> dict:
    return {"session": asdict(cs.session), "memory": asdict(cs.memory),
            "history": _jsonable(cs.history), "pending_notice": cs.pending_notice}


def load_session(d: dict) -> ChatSession:
    m = d["memory"]
    entities = {}
    for eid, e in m["entities"].items():
        ent = EntityState(eid, not_found=e["not_found"])
        ent.facts = {k: [Fact(**f) for f in v] for k, v in e["facts"].items()}
        entities[eid] = ent
    mem = SessionMemory(entities=entities, focus=m["focus"], turns_in_thread=m["turns_in_thread"],
                        consecutive_fallbacks=m.get("consecutive_fallbacks", 0), risk=m.get("risk", 0.0))
    return ChatSession(Session(**d["session"]), mem, d["history"], d["pending_notice"])


# ---------------------------------------------------------------- worker

class Worker:
    def __init__(self, bot: Chatbot, store: SessionStore, worker_id: str | None = None, lease_s: float = 60):
        self.bot, self.store, self.lease_s = bot, store, lease_s
        bot.engine().attach_health(store)  # model breaker state shared across workers
        self.id = worker_id or f"w_{uuid.uuid4().hex[:8]}"

    def start_session(self, tenant_id: str, groups: list[str], role: str = "support") -> str:
        cs = self.bot.new_session(tenant_id, groups, role)
        self.store.create(cs.session.session_id, tenant_id, dump_session(cs))
        return cs.session.session_id

    def handle(self, session_id: str, msg_id: str, text: str) -> dict:
        self.store.claim(session_id, self.id, self.lease_s)          # LeaseHeld if another live owner
        version, state = self.store.load(session_id)
        prior = self.store.begin_turn(session_id, msg_id, self.id, text)
        if prior:                                                     # already answered: no new model call
            return prior
        cs = load_session(state)
        # Heartbeat: renew the lease before each model call so a long but healthy turn
        # keeps ownership. If another worker took over, claim() raises and the turn
        # stops early instead of doing work that could never be committed.
        cs.heartbeat = lambda: self.store.claim(session_id, self.id, self.lease_s)
        turn = self.bot.ask(cs, text)                                 # a crash here commits nothing
        if turn.blocked_by == "unavailable":
            # Dependency outage: leave the turn uncommitted so the client can resend
            # the same msg_id later and get a real answer (not a cached apology).
            return {"answer": turn.answer, "blocked_by": "unavailable", "reasons": turn.reasons,
                    "retry_after_s": 30, "deduplicated": False}
        meta = {"blocked_by": turn.blocked_by, "reasons": turn.reasons, "focus": turn.focus,
                "repaired": turn.repaired, "escalated": turn.escalated,
                "tier": turn.tier, "models": turn.models_used,
                "tools": [c["name"] for c in turn.tools_called]}
        self.store.claim(session_id, self.id, self.lease_s)          # renew right before committing
        new_version = self.store.commit_turn(session_id, msg_id, self.id, version,
                                             dump_session(cs), turn.answer, meta)   # StaleWrite if superseded
        return {"answer": turn.answer, **meta, "version": new_version, "deduplicated": False}

    def recover(self, session_id: str) -> dict:
        """Run on takeover: report what the dead worker left unfinished."""
        self.store.claim(session_id, self.id, self.lease_s)
        return {"incomplete_turns": self.store.incomplete_turns(session_id),
                "unknown_actions": self.store.unknown_actions(session_id)}
