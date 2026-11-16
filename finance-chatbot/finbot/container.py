"""Composition root (dependency injection container).

The ONLY place that knows concrete classes. Everything else receives its
collaborators through the constructor, so tests swap any piece by passing an
override instead of patching modules:

    c = Container(Settings(...), overrides={"guardrail": MyGuard(), "store": SessionStore()})
    c.service.send(token, sid, msg_id, "why did txn_... fail?")

Each component is built lazily, once (singleton per container):

    settings -> engine (ModelOrchestrator; LLM adapters from LLM_PROVIDERS)
             -> guardrail (GUARDRAILS)       -> knowledge (Index or Bedrock KB)
             -> toolbox (txn / log / kb adapters)
             -> chatbot (orchestration layer: guards, gate, tool loop)
             -> store (STORES: sqlite | dynamodb) -> worker -> control plane
             -> verifier + authorizer -> service (trust boundary facade)
"""
from __future__ import annotations

import os
from collections.abc import Callable
from functools import cached_property
from typing import Any

from . import auditlog
from .config import Settings


class Container:
    def __init__(self, settings: Settings | None = None, overrides: dict[str, Any] | None = None,
                 clock: Callable[[], float] | None = None, audit_path: str | None = None):
        self.settings = settings or Settings()
        self.overrides = overrides or {}
        self.clock, self.audit_path = clock, audit_path
        auditlog.configure(self.settings.audit_key)

    def _get(self, name: str, build: Callable[[], Any]) -> Any:
        return self.overrides[name] if name in self.overrides else build()

    # ------------------------------------------------------------------ adapters

    @cached_property
    def engine(self):
        from .llm import make_engine
        return self._get("engine", lambda: make_engine(self.settings))

    @cached_property
    def guardrail(self):
        from .guardrails import make_guardrail
        return self._get("guardrail", lambda: make_guardrail(self.settings))

    @cached_property
    def embedder(self):
        from .registry import EMBEDDERS
        return self._get("embedder", lambda: EMBEDDERS.create(self.settings.embeddings, settings=self.settings))

    @cached_property
    def dataset(self):
        from .app import load_dataset
        from .tools import DEMO_DOCS, DEMO_LOGS, DEMO_TXNS
        return self._get("dataset", lambda: load_dataset(self.settings.data_dir) or (DEMO_TXNS, DEMO_LOGS, DEMO_DOCS))

    @cached_property
    def knowledge(self):
        def build():
            from .knowledge import Index
            s = self.settings
            if s.kb_provider == "vector":
                from .ingestion import VectorKnowledge, embedder_from, store_from
                return VectorKnowledge(store_from(s.vector_store, region=s.aws_region),
                                       embedder_from(s.vector_embedder, region=s.aws_region))
            if s.kb_provider == "bedrock_kb":
                from .ingestion import BedrockKnowledgeBase
                return BedrockKnowledgeBase(s.bedrock_kb_id, s.aws_region)
            if os.path.exists(self.settings.kb_path):
                return Index.load(self.settings.kb_path, self.embedder)
            kb = Index(embed=self.embedder)
            for page in self.dataset[2]:
                kb.upsert_page(page)
            return kb
        return self._get("knowledge", build)

    @cached_property
    def toolbox(self):
        from .domains import enabled
        from .tools import InMemoryLogs, InMemoryTxns, ToolBox
        txns, logs, _ = self.dataset
        return self._get("toolbox", lambda: ToolBox(self._get("txns", lambda: InMemoryTxns(txns)),
                                                    self._get("logs", lambda: InMemoryLogs(logs)),
                                                    self.knowledge, processors=self.processors,
                                                    records=self.records, domains=enabled(self.settings.domains),
                                                    grants=self.grants))

    @cached_property
    def grants(self):
        """Signs the per-turn delegation grant every tool call is checked against."""
        import time

        from .agent_identity import GrantIssuer
        key = self.settings.grant_key.encode() or None
        return self._get("grants", lambda: GrantIssuer(key, clock=self.clock or time.time))

    @cached_property
    def records(self):
        """Records of the domain plug-ins (accounts, transfers, KYC, lending)."""
        from .app import load_records
        from .tools import DEMO_RECORDS, InMemoryRecords
        return self._get("records", lambda: InMemoryRecords(load_records(self.settings.data_dir) or DEMO_RECORDS))

    @cached_property
    def processors(self):
        from .processors import ProcessorDirectory
        return self._get("processors", lambda: ProcessorDirectory.load(self.settings.data_dir))

    @cached_property
    def store(self):
        from .registry import STORES
        return self._get("store", lambda: STORES.create(self.settings.store_provider, settings=self.settings,
                                                        clock=self.clock))

    # ------------------------------------------------------------------ core

    @cached_property
    def chatbot(self):
        from .orchestrator import Chatbot
        return self._get("chatbot", lambda: Chatbot(self.engine, self.guardrail, self.toolbox, self.settings,
                                                    self.audit_path, policies=self.guard_policy,
                                                    tickets=self.tickets))

    @cached_property
    def tickets(self):
        """Where write requests go for a person to execute (the bot never executes them)."""
        from .tickets import DynamoTickets, InMemoryTickets, JsonFileTickets

        def build():
            if self.settings.tickets_path:
                return JsonFileTickets(self.settings.tickets_path)
            if self.settings.store_provider == "dynamodb":   # shared by every worker pod and the desk
                return DynamoTickets(self.store.t)
            return InMemoryTickets()
        return self._get("tickets", build)

    @cached_property
    def ticket_desk(self):
        """The human side: list, claim, resolve (support/admin, audited)."""
        from .tickets import TicketDesk
        return self._get("ticket_desk", lambda: TicketDesk(self.tickets, self.authorizer,
                                                           auditlog.sibling(self.audit_path, ".tickets"),
                                                           self.clock or __import__("time").time))

    @cached_property
    def guard_policy(self):
        """Where turns get the learned guard policy: fixed baseline, or the adaptive
        controller's state in the shared store (FINBOT_GUARD_POLICY=store).
        An override may be a GuardPolicy or a PolicySource."""
        from .adaptive.policy import GuardPolicy
        from .adaptive.source import StaticPolicySource, StorePolicySource

        def build():
            if self.settings.guard_policy_source == "store":
                import time
                return StorePolicySource(self.store, self.clock or time.time, self.settings.control_sync_s)
            return StaticPolicySource()
        src = self._get("guard_policy", build)
        return StaticPolicySource(src) if isinstance(src, GuardPolicy) else src

    @cached_property
    def worker(self):
        from .runtime import Worker
        return self._get("worker", lambda: Worker(self.chatbot, self.store, lease_s=self.settings.lease_s))

    @cached_property
    def authorizer(self):
        from .security import Authorizer
        return self._get("authorizer", Authorizer)

    @cached_property
    def control(self):
        from .control import ControlPlane
        self.worker  # noqa: B018 - attaches shared health/config to the engine first
        return self._get("control", lambda: ControlPlane(self.chatbot.engine(), self.store, self.authorizer,
                                                         auditlog.sibling(self.audit_path, ".control")))

    @cached_property
    def guard_controller(self):
        """Adaptive guardrail controller (finbot/adaptive): labels -> gate -> shadow -> canary -> active."""
        from .adaptive.defaults import default_controller
        return self._get("guard_controller", lambda: default_controller(
            self.store, self.settings.data_dir or "data", self.settings.guard_labels_path, self.authorizer,
            auditlog.sibling(self.audit_path, ".guard"), self.audit_path, self.clock))

    @cached_property
    def verifier(self):
        import time

        from .security import make_verifier
        return self._get("verifier", lambda: make_verifier(self.settings, self.clock or time.time, self.revocations))

    @cached_property
    def revocations(self):
        """Token deny list shared by every pod through the session store."""
        import time

        from .security import Revocations
        return self._get("revocations", lambda: Revocations(self.store, self.clock or time.time))

    @cached_property
    def limiter(self):
        import time

        from .ratelimit import RateLimiter
        s = self.settings
        return self._get("limiter", lambda: RateLimiter(s.rate_user_per_min, s.rate_tenant_per_min,
                                                        self.clock or time.time) if s.rate_user_per_min else None)

    @cached_property
    def service(self):
        from .service import ChatService
        return self._get("service", lambda: ChatService(self.worker, self.verifier, self.control, self.authorizer,
                                                        self.limiter, self.revocations, self.audit_path))

    def close(self) -> None:
        if "store" in self.__dict__:
            self.store.close()
