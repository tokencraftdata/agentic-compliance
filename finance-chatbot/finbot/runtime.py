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

import copy
import logging
import threading
import uuid
from dataclasses import asdict

from . import replies
from .memory import EntityState, Fact, SessionMemory
from .orchestrator import Chatbot, ChatSession
from .store import LeaseHeld, SessionStore
from .tools import Session

log = logging.getLogger("finbot.runtime")

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
                        consecutive_fallbacks=m.get("consecutive_fallbacks", 0), risk=m.get("risk", 0.0),
                        tickets=m.get("tickets", []), user_ids=m.get("user_ids", []),
                        recap=m.get("recap", []))
    return ChatSession(Session(**d["session"]), mem, d["history"], d["pending_notice"])


# ---------------------------------------------------------------- cancellation

class TurnCancelled(Exception):
    """The user (or an operator) cancelled this turn. Raised at the next heartbeat."""


def cancel_key(session_id: str, msg_id: str) -> str:
    return f"cancel:{session_id}:{msg_id}"


# ---------------------------------------------------------------- worker

class Worker:
    def __init__(self, bot: Chatbot, store: SessionStore, worker_id: str | None = None, lease_s: float = 60,
                 action_checker=None):
        self.bot, self.store, self.lease_s = bot, store, lease_s
        # Asks the system of record what became of an external action whose outcome a crashed
        # worker never recorded (store.reconcile). Set it once write tools exist; finbot is
        # read-only today, so there is nothing to reconcile and it stays None.
        self.action_checker = action_checker
        # The store's lease tells processes apart, not threads: one worker id serves every thread
        # of a process. This set stops two threads running a turn of the same session at once.
        self._in_flight: set[str] = set()
        self._in_flight_lock = threading.Lock()
        from .cache import CachedHealth
        ttl = getattr(getattr(bot, "settings", None), "health_cache_s", 1.0)
        # Model breaker state shared across workers, read from the store at most every `ttl` s.
        bot.engine().attach_health(CachedHealth(store, ttl) if ttl > 0 else store)
        self.id = worker_id or f"w_{uuid.uuid4().hex[:8]}"

    def start_session(self, tenant_id: str, groups: list[str], role: str = "support", subject: str = "",
                      scopes: list[str] | None = None) -> str:
        cs = self.bot.new_session(tenant_id, groups, role)
        cs.session.subject, cs.session.scopes = subject, scopes
        self.store.create(cs.session.session_id, tenant_id, dump_session(cs))
        return cs.session.session_id

    def handle(self, session_id: str, msg_id: str, text: str, principal: dict | None = None) -> dict:
        with self._in_flight_lock:
            if session_id in self._in_flight:
                raise LeaseHeld(f"{session_id} is already running a turn in this process")
            self._in_flight.add(session_id)
        try:
            result = self._handle(session_id, msg_id, text, principal)
            # Keep local exclusion through release: another thread has the same worker id.
            # On a failed turn leave the lease to expire, as after a real crash.
            try:
                self.store.release(session_id, self.id)
            except Exception as e:  # noqa: BLE001 - best effort; the lease expires anyway
                log.warning("lease release failed for a session: %s", type(e).__name__)
            return result
        finally:
            with self._in_flight_lock:
                self._in_flight.discard(session_id)

    def _handle(self, session_id: str, msg_id: str, text: str, principal: dict | None = None) -> dict:
        self.store.claim(session_id, self.id, self.lease_s)          # LeaseHeld if another live owner
        version, state = self.store.load(session_id)
        prior = self.store.begin_turn(session_id, msg_id, self.id, text)
        if prior:                                                     # already answered: no new model call
            return prior
        before = copy.deepcopy(state)        # the session shares lists with `state`; keep a clean copy
        cs = load_session(state)
        if principal is not None:
            # The caller's access as of this request (service.py): a role or group taken away
            # since the session started no longer applies to it.
            cs.session.role = principal["role"]
            cs.session.user_groups = list(principal.get("groups") or [])
            cs.session.scopes = principal.get("scopes")
        key = cancel_key(session_id, msg_id)

        def heartbeat():
            # Renew the lease before each model call so a long but healthy turn keeps
            # ownership. If another worker took over, claim() raises and the turn stops early
            # instead of doing work that could never be committed. A cancel flag stops it too.
            self.store.claim(session_id, self.id, self.lease_s)
            if self.store.read_control(key):
                raise TurnCancelled(msg_id)
        cs.heartbeat = heartbeat
        cs.msg_id = msg_id
        if self.action_checker is not None and self.store.unknown_actions(session_id):
            # A previous worker started an external action and died before recording the outcome.
            # Settle it with the system of record before this turn can act again.
            self.store.reconcile(session_id, self.action_checker)
        try:
            if self.store.read_control(key):                          # cancelled before it started
                raise TurnCancelled(msg_id)
            turn = self.bot.ask(cs, text)                             # a crash here commits nothing
            heartbeat()  # also observe cancellation during the final model call or output checks
        except TurnCancelled:
            # Commit the state as it was before the turn, with a fixed answer, so a resend of
            # the same msg_id returns "cancelled" instead of running the turn again.
            # Risk raised during the cancelled turn is kept: cancelling must not wash it out.
            before["memory"]["risk"] = max(before["memory"].get("risk", 0.0), cs.memory.risk)
            meta = {"blocked_by": "cancelled", "reasons": ["cancelled"], "focus": None, "repaired": False,
                    "escalated": False, "tier": None, "models": [], "tools": []}
            self.store.claim(session_id, self.id, self.lease_s)
            new_version = self.store.commit_turn(session_id, msg_id, self.id, version, before,
                                                 replies.CANCELLED, meta)
            return {"answer": replies.CANCELLED, **meta, "version": new_version, "deduplicated": False}
        if turn.blocked_by == "unavailable":
            # Dependency outage: leave the turn uncommitted so the client can resend
            # the same msg_id later and get a real answer (not a cached apology).
            return {"answer": turn.answer, "blocked_by": "unavailable", "reasons": turn.reasons,
                    "retry_after_s": 30, "deduplicated": False}
        meta = {"blocked_by": turn.blocked_by, "reasons": turn.reasons, "focus": turn.focus,
                "repaired": turn.repaired, "escalated": turn.escalated,
                "tier": turn.tier, "models": turn.models_used,
                "tools": [c["name"] for c in turn.tools_called]}
        new_version = self.store.commit_turn(session_id, msg_id, self.id, version,
                                             dump_session(cs), turn.answer, meta)   # StaleWrite if superseded
        return {"answer": turn.answer, **meta, "version": new_version, "deduplicated": False}

    def cancel(self, session_id: str, msg_id: str, by: str = "") -> None:
        """Ask the worker running (or about to run) this turn to stop. Cooperative: it stops at
        its next heartbeat, before the next model call or commit. A tool call in flight finishes; nothing
        is committed but the pre-turn state, and tools are read-only, so stopping is safe."""
        self.store.write_control(cancel_key(session_id, msg_id), {"by": by, "at": self.store.clock()})

    def recover(self, session_id: str) -> dict:
        """Run on takeover: report what the dead worker left unfinished."""
        self.store.claim(session_id, self.id, self.lease_s)
        return {"incomplete_turns": self.store.incomplete_turns(session_id),
                "unknown_actions": self.store.unknown_actions(session_id)}
