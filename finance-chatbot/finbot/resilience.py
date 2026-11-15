"""System retry logic: exponential backoff with jitter, plus a circuit breaker.

Rules:
  * Retry only TRANSIENT failures (timeouts, connection drops, throttling,
    5xx). Never retry a validation error, an auth failure, or a not_found.
  * Retry only idempotent calls. Every finbot tool is read-only; a future write
    tool must go through store.begin_action with a durable op_id instead.
  * Give up fast when a dependency is down (circuit open), and tell the user
    what to do instead of hanging or crashing.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class TransientError(Exception):
    """A failure worth retrying (timeout, throttling, 5xx, connection reset)."""


class ServiceUnavailable(Exception):
    """Dependency still failing after retries, or circuit open."""


class GuardrailUnavailable(ServiceUnavailable):
    """The guardrail service failed: fail closed, never pass unguarded text."""


def is_transient(e: BaseException) -> bool:
    if isinstance(e, (TransientError, TimeoutError, ConnectionError)):
        return True
    name = type(e).__name__
    # Anthropic SDK and botocore names, matched by name so they stay optional imports.
    if name in ("RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError",
                "OverloadedError", "ServiceUnavailableError", "ThrottlingException",
                "ReadTimeoutError", "EndpointConnectionError"):
        return True
    status = getattr(e, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


class CircuitBreaker:
    """Classic three-state breaker.

    closed     calls flow; `threshold` consecutive failures -> open
    open       calls are rejected immediately (fail fast, no load on a sick dependency)
    half_open  after `cooldown_s`, exactly ONE probe call is let through:
               success -> closed; failure -> open again with the cooldown doubled
               (capped at `max_cooldown_s`) so a flapping dependency is probed less often.
    """

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, name: str, threshold: int = 5, cooldown_s: float = 30, max_cooldown_s: float = 300,
                 clock: Callable[[], float] = time.monotonic, shared=None):
        self.name, self.threshold, self.clock = name, threshold, clock
        self.base_cooldown = self.cooldown = cooldown_s
        self.max_cooldown = max_cooldown_s
        self.failures, self.probe_in_flight = 0, False
        self.opened_at: float | None = None
        self.closed_at: float | None = None
        # shared: optional store with read_health/publish_health. Lets every worker see
        # that a model is down (e.g. a replacement after a crash), using a WALL clock.
        self.shared = shared

    def _sync(self) -> None:
        if not self.shared:
            return
        h = self.shared.read_health(self.name)
        if not h:
            return
        if h["opened_at"] is not None and (self.opened_at is None or h["opened_at"] > self.opened_at) \
                and (self.closed_at is None or h["opened_at"] > self.closed_at):
            self.opened_at, self.cooldown = h["opened_at"], h["cooldown"]   # another worker saw it fail
        elif h["opened_at"] is None and self.opened_at is not None and h["updated"] > self.opened_at:
            self.failures, self.opened_at, self.probe_in_flight = 0, None, False  # another worker's probe closed it
            self.cooldown, self.closed_at = self.base_cooldown, h["updated"]

    def _publish(self) -> None:
        if self.shared:
            self.shared.publish_health(self.name, self.opened_at, self.cooldown, self.clock())

    @property
    def state(self) -> str:
        self._sync()
        if self.opened_at is None:
            return self.CLOSED
        return self.HALF_OPEN if self.clock() - self.opened_at >= self.cooldown else self.OPEN

    def available(self) -> bool:
        """Would a call be let through? Read-only: does NOT take the half-open probe slot."""
        s = self.state
        return s == self.CLOSED or (s == self.HALF_OPEN and not self.probe_in_flight)

    def allow(self) -> bool:
        """Admit a call (takes the probe slot when half-open). Call once per attempt."""
        s = self.state
        if s == self.CLOSED:
            return True
        if s == self.HALF_OPEN and not self.probe_in_flight:
            self.probe_in_flight = True  # only one trial request at a time
            return True
        return False

    def success(self) -> None:
        was_open = self.opened_at is not None
        self.failures, self.opened_at, self.probe_in_flight = 0, None, False
        self.cooldown = self.base_cooldown
        if was_open:
            self.closed_at = self.clock()
            self._publish()

    def failure(self) -> None:
        if self.probe_in_flight or self.state == self.HALF_OPEN:  # probe failed: back off harder
            self.cooldown = min(self.max_cooldown, self.cooldown * 2)
            self.opened_at, self.probe_in_flight = self.clock(), False
            self._publish()
            return
        self.failures += 1
        if self.failures >= self.threshold and self.opened_at is None:
            self.opened_at = self.clock()
            self._publish()

    def snapshot(self) -> dict:
        return {"state": self.state, "consecutive_failures": self.failures, "cooldown_s": self.cooldown}


def retry_call(fn: Callable[[], T], *, attempts: int = 3, base_s: float = 0.5, max_s: float = 8.0,
               breaker: CircuitBreaker | None = None, sleep: Callable[[float], None] = time.sleep,
               rng: random.Random | None = None, deadline: float | None = None,
               clock: Callable[[], float] = time.monotonic) -> T:
    """Call fn, retrying transient failures with full-jitter exponential backoff.

    deadline (same timebase as clock): never start an attempt or a backoff sleep
    that would run past it, so retries can't outlive the user's patience or the
    worker's lease."""
    rng = rng or random.Random()
    if deadline is not None and clock() >= deadline:
        raise ServiceUnavailable("turn deadline exceeded")
    if breaker and not breaker.allow():
        raise ServiceUnavailable(f"{breaker.name} circuit open")
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            out = fn()
            if breaker:
                breaker.success()
            return out
        except Exception as e:  # noqa: BLE001 - classified below
            if not is_transient(e):
                raise
            last = e
            if breaker:
                breaker.failure()
                if not breaker.allow():
                    break
            if attempt < attempts - 1:
                delay = rng.uniform(0, min(max_s, base_s * 2 ** attempt))
                if deadline is not None and clock() + delay >= deadline:
                    raise ServiceUnavailable("turn deadline exceeded during retries") from e
                sleep(delay)
    name = breaker.name if breaker else "dependency"
    raise ServiceUnavailable(f"{name} unavailable after retries: {type(last).__name__}") from last
