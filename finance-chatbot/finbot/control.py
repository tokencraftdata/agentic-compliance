"""Control plane for model orchestration.

The data plane (workers answering chats) reads; the control plane writes.
Operators change how the engine behaves for EVERY worker without a deploy:

    status()            per-target health: breaker state, enabled, calls, failovers, p50
    drain(name)         stop sending traffic to a target (maintenance, bad region,
                        a model version under investigation). Refuses to drain the
                        last enabled strong target: there must always be a path.
    restore(name)       put it back; its breaker still decides when it's healthy
    set_routing(mode)   single | tiered (business decision: cheaper model for simple turns)

Config lives in the shared store (control key "model_engine"); each worker's
engine re-reads it at most every control_sync_s. Every change requires an
admin Principal and is audit-logged with who, what, why and when.

Deliberately NOT here: forcing a breaker closed. A breaker opens on real
failures; overriding it sends user traffic into an outage. Restore + the
half-open probe is the safe path back.
"""
from __future__ import annotations

import time

from . import auditlog
from .models import ModelOrchestrator
from .registry import ROUTING
from .security import Authorizer, Principal


class ControlPlane:
    def __init__(self, engine: ModelOrchestrator, store, authorizer: Authorizer | None = None,
                 audit_path: str | None = None):
        self.engine, self.store, self.authz = engine, store, authorizer or Authorizer()
        self.audit_path = audit_path
        self.log: list[dict] = []

    # ------------------------------------------------------------------ reads

    def status(self, who: Principal) -> dict:
        self.authz.can_control(who)
        self.engine.sync_control(force=True)
        cfg = self._cfg()
        return {"routing": (self.engine.routing.name if self.engine.routing else "single"),
                "drained": cfg["drained"], "targets": self.engine.status()}

    # ------------------------------------------------------------------ writes

    def drain(self, who: Principal, name: str, reason: str) -> dict:
        self.authz.can_control(who)
        self.engine.target(name)  # KeyError for unknown targets
        cfg = self._cfg()
        drained = set(cfg["drained"]) | {name}
        if all(t.name in drained for t in self.engine.strong):
            raise ValueError("refusing to drain the last enabled strong model: no path would remain")
        return self._write(who, "drain", {**cfg, "drained": sorted(drained)}, name=name, reason=reason)

    def restore(self, who: Principal, name: str, reason: str = "") -> dict:
        self.authz.can_control(who)
        self.engine.target(name)
        cfg = self._cfg()
        return self._write(who, "restore", {**cfg, "drained": sorted(set(cfg["drained"]) - {name})},
                           name=name, reason=reason)

    def set_routing(self, who: Principal, mode: str, reason: str) -> dict:
        self.authz.can_control(who)
        ROUTING.create(mode)  # validates the name
        if mode == "tiered" and not self.engine.fast:
            raise ValueError("tiered routing needs a fast chain (FINBOT_FAST_MODEL)")
        return self._write(who, "set_routing", {**self._cfg(), "routing": mode}, mode=mode, reason=reason)

    # ------------------------------------------------------------------ internals

    def _cfg(self) -> dict:
        cfg = self.store.read_control(ModelOrchestrator.CONTROL_KEY) or {}
        return {"drained": list(cfg.get("drained", [])), "routing": cfg.get("routing")}

    def _write(self, who: Principal, op: str, cfg: dict, **detail) -> dict:
        self.store.write_control(ModelOrchestrator.CONTROL_KEY, cfg)
        self.engine.sync_control(force=True)
        rec = {"ts": time.time(), "actor": who.subject, "op": op, **detail, "config": cfg}
        self.log.append(rec)
        auditlog.write(self.audit_path, "control", rec)
        return rec
