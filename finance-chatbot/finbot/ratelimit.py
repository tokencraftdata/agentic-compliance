"""Rate limits per user and per tenant (token bucket), checked after authentication.

Two buckets per request: the caller's subject and the caller's tenant. A single user
cannot exhaust their tenant's budget, and one tenant cannot exhaust the model budget
for everyone else. A refused request costs no model call.

The WAF's rate-based rule (infra/) limits by client IP before traffic reaches the
service; this limits by verified identity, which an attacker rotating IPs cannot dodge.

Buckets are kept in process. Each API pod enforces its own share; set limits to
(budget / replicas), or back BucketStore with a shared counter (DynamoDB atomic
update, Redis INCR) when exact global limits matter.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


class RateLimited(Exception):
    """429. `retry_after_s` goes in the Retry-After header."""

    def __init__(self, scope: str, retry_after_s: float):
        super().__init__(f"rate limit exceeded ({scope}); retry after {retry_after_s:.0f}s")
        self.scope, self.retry_after_s = scope, retry_after_s


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucket:
    def __init__(self, per_minute: float, burst: float | None = None, clock: Callable[[], float] = time.time,
                 max_keys: int = 100_000):
        if per_minute <= 0:
            raise ValueError("per_minute must be positive")
        self.rate = per_minute / 60.0
        self.burst = burst if burst is not None else per_minute
        self.clock, self.max_keys = clock, max_keys
        self._b: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def take(self, key: str) -> float:
        """0 if allowed (a token is spent), else seconds until one is available."""
        now = self.clock()
        with self._lock:
            b = self._b.get(key)
            if b is None:
                if len(self._b) >= self.max_keys:   # bound memory: drop full (idle) buckets
                    self._b = {k: v for k, v in self._b.items() if v.tokens < self.burst}
                b = self._b[key] = _Bucket(self.burst, now)
            b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.rate)
            b.updated = now
            if b.tokens >= 1:
                b.tokens -= 1
                return 0.0
            return (1 - b.tokens) / self.rate


class RateLimiter:
    def __init__(self, user_per_min: float, tenant_per_min: float, clock: Callable[[], float] = time.time):
        self.user = TokenBucket(user_per_min, clock=clock)
        self.tenant = TokenBucket(tenant_per_min, clock=clock)

    def check(self, subject: str, tenant_id: str) -> None:
        for scope, bucket, key in (("user", self.user, f"{tenant_id}/{subject}"), ("tenant", self.tenant, tenant_id)):
            wait = bucket.take(key)
            if wait:
                raise RateLimited(scope, math.ceil(wait))
