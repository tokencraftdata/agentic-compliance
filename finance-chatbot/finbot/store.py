"""Durable session store: the shared case record that survives a worker crash.

What it stores (per session):
  * state     typed ledger, focus, pending notices, append-only history (JSON)
  * version   increases on every committed turn (optimistic concurrency)
  * owner     worker holding the lease, and lease expiry
  * turns     journal keyed by client message id: started -> committed
  * actions   external side effects keyed by a durable operation id
              (pending -> succeeded/failed; pending at crash time -> unknown)

Guarantees:
  * A turn's effects are visible only after commit_turn. A crash mid-turn
    leaves the last committed state intact; the replacement re-runs the turn
    (safe because tools are read-only).
  * commit_turn is a compare-and-swap on (version, owner, live lease). A delayed
    worker that comes back after takeover cannot overwrite newer work.
  * The same message id is answered once: a retry after a post-commit crash
    returns the stored answer without calling the model again.
  * An external action whose outcome was not recorded is marked unknown and
    must be reconciled against the system of record before any retry.

SQLite (stdlib) keeps this runnable anywhere. Production mapping: DynamoDB
(ConditionExpression on version/owner, TTL attribute) or Postgres
(SELECT ... FOR UPDATE / version column). Same method contract.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable

from .crypto import Codec, PlainCodec, ctx, session_tenant
from .pii import mask_pii


class StoreError(Exception):
    pass


class LeaseHeld(StoreError):
    """Another live worker owns this session."""


class StaleWrite(StoreError):
    """Version or lease changed since this worker loaded the session."""


class SessionExpired(StoreError):
    pass


MAX_SESSION_LIFETIME_S = 86400  # idle expiry still applies; a new session is required after one day


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, version INTEGER NOT NULL,
  owner TEXT, lease_until REAL, state TEXT NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL,
  hard_expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
  session_id TEXT NOT NULL, msg_id TEXT NOT NULL, turn_no INTEGER NOT NULL, status TEXT NOT NULL,
  worker TEXT, attempts INTEGER NOT NULL DEFAULT 1, user_text TEXT, answer TEXT, meta TEXT,
  started REAL, committed REAL, PRIMARY KEY (session_id, msg_id));
CREATE TABLE IF NOT EXISTS model_health (
  name TEXT PRIMARY KEY, opened_at REAL, cooldown REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS control (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS counters (
  key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS actions (
  op_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, msg_id TEXT, kind TEXT NOT NULL,
  params TEXT, status TEXT NOT NULL, result TEXT, updated REAL NOT NULL);
"""


class SessionStore:
    def __init__(self, path: str = ":memory:", clock: Callable[[], float] = time.time,
                 idle_ttl_s: float = 1800, codec: Codec | None = None):
        # codec: session state, user text and answers are sealed with it (finbot/crypto.py)
        self.codec: Codec = codec or PlainCodec()
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.clock, self.idle_ttl = clock, idle_ttl_s
        self._lock = threading.Lock()
        # Serialize migration across processes opening an existing database at the same time.
        # Old SQLite journals have no TTL; retain them while bounding further session activity.
        with self._tx() as db:
            if "hard_expires" not in {r[1] for r in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN hard_expires REAL NOT NULL DEFAULT 0")
                db.execute("UPDATE sessions SET hard_expires = updated + ?", (MAX_SESSION_LIFETIME_S,))

    def close(self) -> None:
        self.db.close()

    # ---------------------------------------------------------------- helpers

    def _tx(self):
        store = self

        class Tx:
            def __enter__(self):
                store._lock.acquire()
                store.db.execute("BEGIN IMMEDIATE")
                return store.db

            def __exit__(self, exc_type, *_):
                try:
                    store.db.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()
        return Tx()

    def _row(self, db, session_id):
        r = db.execute("SELECT tenant_id, version, owner, lease_until, state, expires, hard_expires FROM sessions "
                       "WHERE session_id=?", (session_id,)).fetchone()
        if r is None:
            raise KeyError(session_id)
        if min(r[5], r[6]) <= self.clock():
            raise SessionExpired(session_id)
        return r[:6]

    # ---------------------------------------------------------------- sessions

    def create(self, session_id: str, tenant_id: str, state: dict) -> None:
        if (named := session_tenant(session_id)) is not None and named != tenant_id:
            raise ValueError("session id names a different tenant")   # the id's tenant is trusted later
        now = self.clock()
        with self._tx() as db:
            db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                       (session_id, tenant_id, 0, None, None, self.codec.seal(state, ctx(session_id, "session_state")),
                        now, now + min(self.idle_ttl, MAX_SESSION_LIFETIME_S), now + MAX_SESSION_LIFETIME_S))

    def load(self, session_id: str) -> tuple[int, dict]:
        with self._tx() as db:
            _t, version, _o, _l, state, _e = self._row(db, session_id)
        return version, self.codec.open(state, ctx(session_id, "session_state"))

    def tenant_of(self, session_id: str) -> str:
        """Owner tenant of a session: the API layer checks it against the caller's token."""
        with self._tx() as db:
            return self._row(db, session_id)[0]

    def claim(self, session_id: str, worker: str, lease_s: float = 30) -> int:
        """Take or renew ownership. Fails while another worker's lease is live."""
        now = self.clock()
        with self._tx() as db:
            _t, version, owner, lease_until, _s, _e = self._row(db, session_id)
            if owner not in (None, worker) and lease_until and lease_until > now:
                raise LeaseHeld(f"{session_id} held by {owner} until {lease_until:.0f}")
            db.execute("UPDATE sessions SET owner=?, lease_until=? WHERE session_id=?",
                       (worker, now + lease_s, session_id))
            if owner not in (None, worker):
                # Takeover: anything the previous owner started becomes uncertain.
                db.execute("UPDATE actions SET status='unknown', updated=? "
                           "WHERE session_id=? AND status='pending'", (now, session_id))
        return version

    def release(self, session_id: str, worker: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE sessions SET owner=NULL, lease_until=NULL WHERE session_id=? AND owner=?",
                       (session_id, worker))

    # ---------------------------------------------------------------- turns

    def begin_turn(self, session_id: str, msg_id: str, worker: str, user_text: str) -> dict | None:
        """Journal the start of a turn. Returns the stored result if already committed."""
        user_text = mask_pii(user_text)[0]   # the journal never holds card data
        now = self.clock()
        with self._tx() as db:
            r = db.execute("SELECT status, answer, meta FROM turns WHERE session_id=? AND msg_id=?",
                           (session_id, msg_id)).fetchone()
            if r and r[0] == "committed":
                meta = self.codec.open(r[2], ctx(session_id, "turn_meta")) if r[2] else {}
                return {"answer": self.codec.open(r[1], ctx(session_id, "answer")), **meta, "deduplicated": True}
            if r:  # started by a worker that crashed (or by us earlier): retry it
                db.execute("UPDATE turns SET worker=?, attempts=attempts+1, started=? "
                           "WHERE session_id=? AND msg_id=?", (worker, now, session_id, msg_id))
            else:
                n = db.execute("SELECT COUNT(*) FROM turns WHERE session_id=?", (session_id,)).fetchone()[0]
                db.execute("INSERT INTO turns (session_id,msg_id,turn_no,status,worker,user_text,started) "
                           "VALUES (?,?,?,?,?,?,?)", (session_id, msg_id, n + 1, "started", worker,
                                                   self.codec.seal(user_text, ctx(session_id, "user_text")), now))
        return None

    def reply(self, session_id: str, msg_id: str) -> dict | None:
        """The committed answer for one message, or None while it is not committed. Read-only:
        the API polls this in queue mode and never runs the turn itself."""
        r = self.db.execute("SELECT status, answer, meta FROM turns WHERE session_id=? AND msg_id=?",
                            (session_id, msg_id)).fetchone()
        if not r or r[0] != "committed":
            return None
        meta = self.codec.open(r[2], ctx(session_id, "turn_meta")) if r[2] else {}
        return {"answer": self.codec.open(r[1], ctx(session_id, "answer")), **meta}

    def commit_turn(self, session_id: str, msg_id: str, worker: str, expected_version: int,
                    state: dict, answer: str, meta: dict) -> int:
        """Atomically persist the new state and mark the turn committed."""
        now = self.clock()
        with self._tx() as db:
            _t, version, owner, lease_until, _s, _e = self._row(db, session_id)
            if version != expected_version:
                raise StaleWrite(f"version {version} != expected {expected_version}")
            if owner != worker or not lease_until or lease_until < now:
                raise StaleWrite(f"lease lost (owner={owner})")
            db.execute("UPDATE sessions SET state=?, version=?, updated=?, expires=MIN(?, hard_expires) "
                       "WHERE session_id=?",
                       (self.codec.seal(state, ctx(session_id, "session_state")), version + 1, now,
                        now + self.idle_ttl, session_id))
            db.execute("UPDATE turns SET status='committed', answer=?, meta=?, committed=? "
                       "WHERE session_id=? AND msg_id=?",
                       (self.codec.seal(answer, ctx(session_id, "answer")),
                        self.codec.seal(meta, ctx(session_id, "turn_meta")), now, session_id, msg_id))
        return version + 1

    def incomplete_turns(self, session_id: str) -> list[dict]:
        rows = self.db.execute("SELECT msg_id, worker, attempts, user_text FROM turns "
                               "WHERE session_id=? AND status='started'", (session_id,)).fetchall()
        return [{"msg_id": m, "worker": w, "attempts": a, "user_text": self.codec.open(u, ctx(session_id, "user_text"))}
                for m, w, a, u in rows]

    def turns(self, session_id: str) -> list[dict]:
        rows = self.db.execute("SELECT msg_id, turn_no, status, worker, attempts FROM turns "
                               "WHERE session_id=? ORDER BY turn_no", (session_id,)).fetchall()
        return [dict(zip(("msg_id", "turn_no", "status", "worker", "attempts"), r, strict=True)) for r in rows]

    # ---------------------------------------------------------------- shared model health

    def publish_health(self, name: str, opened_at: float | None, cooldown: float, updated: float) -> None:
        """Share a model breaker's open/closed state with every worker (last write wins)."""
        with self._tx() as db:
            db.execute("INSERT INTO model_health VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                       "opened_at=excluded.opened_at, cooldown=excluded.cooldown, updated=excluded.updated "
                       "WHERE excluded.updated >= model_health.updated", (name, opened_at, cooldown, updated))

    def read_health(self, name: str) -> dict | None:
        r = self.db.execute("SELECT opened_at, cooldown, updated FROM model_health WHERE name=?", (name,)).fetchone()
        return {"opened_at": r[0], "cooldown": r[1], "updated": r[2]} if r else None

    # ---------------------------------------------------------------- control plane config

    def write_control(self, key: str, value: dict) -> None:
        with self._tx() as db:
            db.execute("INSERT INTO control VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                       "value=excluded.value, updated=excluded.updated", (key, json.dumps(value), self.clock()))

    def read_control(self, key: str) -> dict | None:
        r = self.db.execute("SELECT value FROM control WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    # ---------------------------------------------------------------- shared counters (rate limits)

    def incr_counter(self, key: str, ttl_s: float) -> int:
        now = self.clock()
        with self._tx() as db:
            db.execute("DELETE FROM counters WHERE expires < ?", (now - 3600,))   # keep the table small
            db.execute("INSERT INTO counters VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count = count + 1",
                       (key, now + ttl_s))
            return int(db.execute("SELECT count FROM counters WHERE key=?", (key,)).fetchone()[0])

    def read_counter(self, key: str) -> int:
        r = self.db.execute("SELECT count, expires FROM counters WHERE key=?", (key,)).fetchone()
        return int(r[0]) if r and r[1] > self.clock() else 0

    # ---------------------------------------------------------------- side effects

    def begin_action(self, op_id: str, session_id: str, msg_id: str, kind: str, params: dict) -> str:
        """Record intent BEFORE calling the external system. Returns existing status if seen."""
        now = self.clock()
        with self._tx() as db:
            r = db.execute("SELECT status FROM actions WHERE op_id=?", (op_id,)).fetchone()
            if r:
                return r[0]
            db.execute("INSERT INTO actions VALUES (?,?,?,?,?,?,?,?)",
                       (op_id, session_id, msg_id, kind, json.dumps(params), "pending", None, now))
        return "new"

    def finish_action(self, op_id: str, status: str, result: dict) -> None:
        with self._tx() as db:
            db.execute("UPDATE actions SET status=?, result=?, updated=? WHERE op_id=?",
                       (status, json.dumps(result), self.clock(), op_id))

    def unknown_actions(self, session_id: str) -> list[dict]:
        rows = self.db.execute("SELECT op_id, kind, params FROM actions WHERE session_id=? AND status='unknown'",
                               (session_id,)).fetchall()
        return [{"op_id": o, "kind": k, "params": json.loads(p)} for o, k, p in rows]

    def reconcile(self, session_id: str, check: Callable[[dict], dict | None]) -> list[dict]:
        """Ask the system of record what happened to each unknown action.

        check(action) returns {"status": "succeeded"|"failed", ...} or None if the
        external system has no record (then it never happened and may be retried
        with the SAME op_id as its idempotency key)."""
        out = []
        for a in self.unknown_actions(session_id):
            res = check(a)
            status = res["status"] if res else "failed"
            self.finish_action(a["op_id"], status, res or {"reason": "no record at provider"})
            out.append({**a, "status": status})
        return out
