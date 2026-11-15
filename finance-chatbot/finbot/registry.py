"""Factories: one registry per port, keyed by provider name from config.

    LLM_PROVIDERS   bedrock | anthropic | fake            -> LLMClient
    GUARDRAILS      bedrock | local                       -> Guardrail
    STORES          sqlite | dynamodb                     -> SessionRepository
    EMBEDDERS       none | titan                          -> Embedder | None
    ROUTING         single | tiered                       -> RoutingStrategy

Vendor SDKs are imported lazily inside each factory, so a test run needs
neither boto3 nor anthropic. A new adapter registers itself with
`@REGISTRY.register("name")` and becomes selectable by env var, with no change
to the orchestration code (open/closed principle).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str):
        self.kind = kind
        self._factories: dict[str, Callable[..., T]] = {}

    def register(self, name: str):
        def deco(factory: Callable[..., T]) -> Callable[..., T]:
            if name in self._factories:
                raise ValueError(f"{self.kind} provider {name!r} already registered")
            self._factories[name] = factory
            return factory
        return deco

    def create(self, name: str, **kw) -> T:
        try:
            factory = self._factories[name]
        except KeyError:
            raise ValueError(f"unknown {self.kind} provider {name!r}; "
                             f"known: {', '.join(sorted(self._factories))}") from None
        return factory(**kw)

    def names(self) -> list[str]:
        return sorted(self._factories)


LLM_PROVIDERS: Registry = Registry("model")
GUARDRAILS: Registry = Registry("guardrail")
STORES: Registry = Registry("session store")
EMBEDDERS: Registry = Registry("embedder")
ROUTING: Registry = Registry("routing")


# ------------------------------------------------------------------ built-in adapters

@LLM_PROVIDERS.register("bedrock")
def _bedrock_llm(model: str, region: str = "", effort: str = "medium", **_):
    from .llm import ClaudeLLM
    return ClaudeLLM("bedrock", model, region, effort)


@LLM_PROVIDERS.register("anthropic")
def _anthropic_llm(model: str, region: str = "", effort: str = "medium", **_):
    from .llm import ClaudeLLM
    return ClaudeLLM("anthropic", model, region, effort)


@LLM_PROVIDERS.register("fake")
def _fake_llm(**_):
    from .llm import FakeLLM
    return FakeLLM()


@GUARDRAILS.register("bedrock")
def _bedrock_guard(settings, **_):
    from .guardrails import BedrockGuardrail
    return BedrockGuardrail(settings.guardrail_id, settings.guardrail_version, settings.aws_region)


@GUARDRAILS.register("local")
def _local_guard(**_):
    from .guardrails import LocalGuardrail
    return LocalGuardrail()


@STORES.register("sqlite")
def _sqlite_store(settings, clock=None, **_):
    import time

    from .store import SessionStore
    return SessionStore(settings.store_path, clock=clock or time.time)


@STORES.register("dynamodb")
def _dynamo_store(settings, clock=None, **_):
    import time

    from .store_dynamo import Boto3Dynamo, DynamoSessionStore
    return DynamoSessionStore(Boto3Dynamo(settings.dynamo_table, settings.aws_region), clock=clock or time.time)


@EMBEDDERS.register("none")
def _no_embed(**_):
    return None


@EMBEDDERS.register("titan")
def _titan(settings, **_):
    from .knowledge import titan_embedder
    return titan_embedder(settings.aws_region, settings.embed_model)


@ROUTING.register("single")
def _single(**_):
    from .models import SingleChainRouting
    return SingleChainRouting()


@ROUTING.register("tiered")
def _tiered(**_):
    from .models import TieredRouting
    return TieredRouting()
