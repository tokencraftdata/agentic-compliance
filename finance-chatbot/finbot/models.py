"""Model orchestrator engine: one `create()` in front of several models.

  chain      ordered targets, e.g. Bedrock us-east-1 -> Bedrock us-west-2 -> Claude API.
             Each target has its own circuit breaker and retry budget.
  failover   a target that keeps failing (429 / 5xx / timeouts) opens its breaker;
             later calls skip it without waiting and go to the next target, until a
             half-open probe shows it has recovered. Failover can happen mid-turn: the
             next model continues from the same append-only history.
  refusal    a `stop_reason: "refusal"` can be retried once on the next target
             (on Bedrock there is no server-side fallback, so we do it here).
  routing    optional tiers: route simple turns to a "fast" chain and hard turns to
             the default ("strong") chain. Off by default: choosing a cheaper model is
             a business decision, enable it with FINBOT_ROUTING=tiered.
  health     per-target calls, failures, failovers, refusals, latency, breaker state.

Notes:
  * Prompt caches are per model, so a failover or tier change starts a cold cache.
  * Thinking blocks written by one model are passed back unchanged; the API drops,
    unbilled, any block the next model cannot read.
  * Non-transient errors (400 validation, auth) are NOT failed over: they are bugs
    or config problems and are raised so they get fixed.
"""
from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

from .ports import LLMClient
from .resilience import CircuitBreaker, ServiceUnavailable, retry_call


@dataclass
class ModelTarget:
    name: str
    client: LLMClient              # anything with create(system, messages, tools)
    tier: str = "strong"
    attempts: int = 3              # transient retries on this target before failing over
    refusal_fallback: bool = True  # try the next target if this one refuses
    breaker: CircuitBreaker | None = None
    enabled: bool = True           # control plane: a drained target is skipped by every worker
    stats: dict = field(default_factory=lambda: {"calls": 0, "ok": 0, "failures": 0, "refusals": 0,
                                                  "served_as_failover": 0, "latency_ms": []})

    @property
    def circuit(self) -> CircuitBreaker:
        """The target's breaker; ModelOrchestrator always assigns one."""
        if self.breaker is None:
            raise RuntimeError(f"{self.name} has no circuit breaker; build targets through ModelOrchestrator")
        return self.breaker


class ModelOrchestrator:
    def __init__(self, targets: list[ModelTarget], fast: list[ModelTarget] | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 breaker_threshold: int = 3, cooldown_s: float = 30, rng: random.Random | None = None,
                 routing: RoutingStrategy | None = None, control_sync_s: float = 2.0):
        if not targets:
            raise ValueError("at least one model target is required")
        self.strong, self.fast = targets, fast or []
        # None = let the caller decide (Chatbot falls back to settings.routing).
        self.routing: RoutingStrategy | None = routing
        self.shared = None                     # HealthStore once attach_health() is called
        self.control_sync_s, self._synced_at = control_sync_s, None
        self.sleep, self.rng, self.clock = sleep, rng or random.Random(), clock
        for t in self.strong + self.fast:
            t.breaker = t.breaker or CircuitBreaker(f"model:{t.name}", threshold=breaker_threshold,
                                                    cooldown_s=cooldown_s, clock=clock)
        self.last_target: str | None = None

    # ------------------------------------------------------------------ routing

    def route(self, text: str, txn_ids_in_text: int, prior_fallbacks: int, repairing: bool) -> str | None:
        """Strategy pattern: the routing policy decides the tier; None = default chain."""
        self.sync_control()
        return (self.routing or SingleChainRouting()).tier(text, txn_ids_in_text, prior_fallbacks, repairing)

    def targets(self) -> list[ModelTarget]:
        return self.strong + [f for f in self.fast if f not in self.strong]

    def target(self, name: str) -> ModelTarget:
        for t in self.targets():
            if t.name == name:
                return t
        raise KeyError(f"no model target {name!r}; known: {[t.name for t in self.targets()]}")

    def chain_for(self, tier: str | None) -> list[ModelTarget]:
        """Preferred tier first, then the strong chain as fallback (deduplicated)."""
        first = self.fast if tier == "fast" and self.fast else self.strong
        out = list(first)
        out += [t for t in self.strong if t not in out]
        return out

    # ------------------------------------------------------------------ call

    def create(self, system: str, messages: list, tools: list[dict], tier: str | None = None,
               deadline: float | None = None):
        self.sync_control()
        chain = self.chain_for(tier)
        errors: list[str] = []
        refused = None
        for i, target in enumerate(chain):
            if deadline is not None and self.clock() >= deadline:
                errors.append("turn deadline exceeded")  # don't start another model past the deadline
                break
            if not target.enabled:
                errors.append(f"{target.name}:drained")
                continue
            if not target.circuit.available():  # read-only check; retry_call takes the probe slot
                errors.append(f"{target.name}:circuit_{target.circuit.state}")
                continue
            target.stats["calls"] += 1
            t0 = time.monotonic()
            try:
                resp = retry_call(partial(target.client.create, system, messages, tools),
                                  attempts=target.attempts, base_s=1.0, max_s=16,
                                  breaker=target.breaker, sleep=self.sleep, rng=self.rng,
                                  deadline=deadline, clock=self.clock)
            except ServiceUnavailable as e:
                target.stats["failures"] += 1
                errors.append(f"{target.name}:{e}")
                continue
            target.stats["latency_ms"] = (target.stats["latency_ms"] + [int((time.monotonic() - t0) * 1000)])[-200:]
            if getattr(resp, "stop_reason", None) == "refusal" and target.refusal_fallback and i < len(chain) - 1:
                target.stats["refusals"] += 1
                refused = resp
                errors.append(f"{target.name}:refusal")
                continue
            target.stats["ok"] += 1
            if i > 0 or errors:
                target.stats["served_as_failover"] += 1
            self.last_target = target.name
            return resp
        if refused is not None:  # every other target failed; surface the refusal itself
            self.last_target = chain[0].name
            return refused
        raise ServiceUnavailable("all models unavailable: " + "; ".join(errors))

    # ------------------------------------------------------------------ health

    def attach_health(self, store) -> None:
        """Share breaker state through the session store, so all workers (and a
        replacement after a crash) skip a model that another worker found down.
        Switches breakers and deadlines to the store's wall clock."""
        self.clock, self.shared, self._synced_at = store.clock, store, None
        for t in self.strong + self.fast:
            t.circuit.shared, t.circuit.clock = store, store.clock

    # ------------------------------------------------------------------ control plane hooks

    CONTROL_KEY = "model_engine"

    def sync_control(self, force: bool = False) -> None:
        """Apply shared control-plane config (drained targets, routing mode).
        Read at most every control_sync_s per worker, so the store isn't hit per call."""
        if not self.shared:
            return
        now = self.clock()
        if not force and self._synced_at is not None and now - self._synced_at < self.control_sync_s:
            return
        self._synced_at = now
        cfg = self.shared.read_control(self.CONTROL_KEY)
        if not cfg:
            return
        drained = set(cfg.get("drained", []))
        for t in self.targets():
            t.enabled = t.name not in drained
        mode = cfg.get("routing")
        if mode and (self.routing is None or mode != self.routing.name):
            from .registry import ROUTING
            self.routing = ROUTING.create(mode)

    def status(self) -> list[dict]:
        out = []
        for t in self.targets():
            lat = sorted(t.stats["latency_ms"])
            out.append({"model": t.name, "tier": t.tier, "enabled": t.enabled, **t.circuit.snapshot(),
                        **{k: v for k, v in t.stats.items() if k != "latency_ms"},
                        "p50_ms": lat[len(lat) // 2] if lat else None})
        return out


# ---------------------------------------------------------------- routing strategies

class RoutingStrategy:
    """Strategy: picks the tier for a turn. Implementations are stateless."""
    name = "base"

    def tier(self, text: str, txn_ids_in_text: int, prior_fallbacks: int, repairing: bool) -> str | None:
        raise NotImplementedError


class SingleChainRouting(RoutingStrategy):
    """Every turn uses the default (strong) chain. The default: no silent downgrade."""
    name = "single"

    def tier(self, *_a, **_k) -> str | None:
        return None


class TieredRouting(RoutingStrategy):
    """Simple turns to the fast chain, hard turns and every repair to the strong chain."""
    name = "tiered"

    def tier(self, text, txn_ids_in_text, prior_fallbacks, repairing) -> str | None:
        return route_tier(text, txn_ids_in_text, prior_fallbacks, repairing)


# ---------------------------------------------------------------- complexity router

HARD_WORDS = ("dispute", "chargeback", "refund", "compare", "difference between", "reconcile",
              "settlement", "why were", "all of", "every", "pattern")


def route_tier(text: str, txn_ids_in_text: int, prior_fallbacks: int, repairing: bool) -> str:
    """Pick a tier in code (cheap, deterministic, auditable)."""
    low = text.lower()
    if repairing or prior_fallbacks > 0:
        return "strong"            # the cheap path already failed: escalate the model too
    if txn_ids_in_text >= 2 or any(w in low for w in HARD_WORDS) or len(low.split()) > 40:
        return "strong"
    return "fast"


# ---------------------------------------------------------------- config

def parse_chain(spec: str, make_client: Callable[[str, str, str], LLMClient], tier: str) -> list[ModelTarget]:
    """'bedrock:anthropic.claude-opus-5-5@us-east-1, anthropic:claude-opus-5-5' -> targets."""
    targets = []
    for raw in filter(None, (p.strip() for p in spec.split(","))):
        provider, _, rest = raw.partition(":")
        model, _, region = rest.partition("@")
        name = f"{provider}:{model}" + (f"@{region}" if region else "")
        targets.append(ModelTarget(name, make_client(provider, model, region), tier=tier))
    return targets
