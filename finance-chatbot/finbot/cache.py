"""Cache: a port, two adapters, and the only two things finbot caches.

What may be cached is decided here, not by callers. The rule: only data that is safe to be a
little stale and holds no customer record.

    cached                                  never cached
    help-page search results (minutes)      any record tool (get_transaction, get_<record>, logs)
    model breaker health (about 1 s)        model answers, verified or not
                                            session state, tickets, grants, tokens

A record answer must come from the system of record on every turn: a cached "approved" after
the record changed to "reversed" is exactly the wrong answer the gate exists to stop. A semantic
answer cache has the same flaw, so finbot has none.

Backends (FINBOT_CACHE):
    memory   per-process TTL + LRU; the default; nothing to run
    redis    any Redis-protocol server: ElastiCache for Valkey or Redis OSS, MemoryDB. Shared by
             every pod, so one pod's search warms the others. Needs `pip install redis`.
    none     caching off

A cache failure is a miss, never an error: a turn must not fail because the cache is down.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol

log = logging.getLogger("finbot.cache")


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_s: float) -> None: ...


class NullCache:
    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        return None


class MemoryCache:
    """Thread-safe TTL cache with an LRU bound. Values are stored as JSON copies, so a caller
    that mutates what it got back cannot change what the next caller sees."""

    def __init__(self, max_entries: int = 2048, clock: Callable[[], float] = time.monotonic):
        self.max_entries, self.clock = max_entries, clock
        self._data: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None or item[0] <= self.clock():
                if item is not None:
                    del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return json.loads(item[1])

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        if ttl_s <= 0:
            return
        with self._lock:
            self._data[key] = (self.clock() + ttl_s, json.dumps(value))
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


class RedisCache:
    """Redis-protocol backend (ElastiCache for Valkey/Redis OSS, MemoryDB). Use TLS
    (`rediss://`) and IAM or AUTH as the cluster requires. Every error is logged and treated
    as a miss."""

    def __init__(self, url: str, prefix: str = "finbot:", client=None, timeout_s: float = 0.05):
        if client is None:
            import redis  # optional dependency
            client = redis.Redis.from_url(url, socket_timeout=timeout_s, socket_connect_timeout=timeout_s)
        self.client, self.prefix = client, prefix

    def get(self, key: str) -> Any | None:
        try:
            raw = self.client.get(self.prefix + key)
        except Exception as e:  # noqa: BLE001 - a cache outage is a miss
            log.warning("cache get failed: %s", type(e).__name__)
            return None
        return None if raw is None else json.loads(raw)

    def set(self, key: str, value: Any, ttl_s: float) -> None:
        if ttl_s <= 0:
            return
        try:
            self.client.set(self.prefix + key, json.dumps(value), px=int(ttl_s * 1000))
        except Exception as e:  # noqa: BLE001
            log.warning("cache set failed: %s", type(e).__name__)


def make_cache(kind: str, url: str = "") -> Cache:
    if kind == "none":
        return NullCache()
    if kind == "memory":
        return MemoryCache()
    if kind == "redis":
        if not url:
            raise ValueError("FINBOT_CACHE=redis needs FINBOT_CACHE_URL")
        return RedisCache(url)
    raise ValueError(f"unknown cache {kind!r}; known: memory, none, redis")


def key_of(*parts: Any) -> str:
    """A stable key that holds no raw text: query words and group names are hashed."""
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------ what is cached


class CachedKnowledge:
    """Help-page search with a cache in front. The key covers the normalised query, k, the
    caller's groups (restricted pages differ by group) and the index generation, so a
    re-ingest invalidates every entry at once. Results are cached before screening;
    ToolResultScreen still screens every hit on every turn."""

    def __init__(self, kb, cache: Cache, ttl_s: float = 300):
        self.kb, self.cache, self.ttl_s = kb, cache, ttl_s

    def __getattr__(self, name):          # everything else (upsert_page, save, ...) goes to the index
        return getattr(self.kb, name)

    def search(self, query: str, k: int = 4, groups: list[str] | None = None, **kw):
        norm = " ".join(query.lower().split())
        key = "kb:" + key_of(norm, k, sorted(groups or []), getattr(self.kb, "generation", None), sorted(kw.items()))
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        result = self.kb.search(query, k=k, groups=groups, **kw)
        self.cache.set(key, result, self.ttl_s)
        return result


class CachedHealth:
    """Model breaker health read through a short per-process cache. Without it every breaker
    check is a store read (DynamoDB in production). A worker's own publish updates its copy at
    once; other workers see it within `ttl_s`. Everything else goes straight to the store."""

    def __init__(self, store, ttl_s: float = 1.0):
        self.store, self.ttl_s = store, ttl_s
        self._local = MemoryCache(max_entries=256, clock=store.clock)

    def __getattr__(self, name):
        return getattr(self.store, name)

    def read_health(self, name: str):
        hit = self._local.get(name)
        if hit is not None:
            return hit or None              # {} marks "nothing stored", so it is cached too
        h = self.store.read_health(name)
        self._local.set(name, h or {}, self.ttl_s)
        return h

    def publish_health(self, name: str, opened_at, cooldown, updated) -> None:
        self.store.publish_health(name, opened_at, cooldown, updated)
        self._local.delete(name)
