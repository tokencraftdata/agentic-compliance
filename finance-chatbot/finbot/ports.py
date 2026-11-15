"""Ports: the contracts every layer depends on (hexagonal / ports-and-adapters).

The core (orchestrator, model engine, verification) imports only these
Protocols. Concrete adapters (Bedrock, Claude API, SQLite, DynamoDB, SQS, ...)
implement them and are chosen in one place, the composition root
(finbot/container.py), through the factories in finbot/registry.py.

    layer            depends on                      never on
    ---------------  ------------------------------  -------------------------
    api / service    ports, security                 boto3, anthropic
    orchestration    ports, verification, models     concrete adapters
    adapters         vendor SDKs                     orchestration internals

Swapping a vendor (another region, DynamoDB instead of SQLite, a different
guardrail) is a config change plus, at most, one new adapter class.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """One model endpoint. Returns an Anthropic Messages-shaped response
    (.content blocks with .type/.text/.id/.name/.input, and .stop_reason)."""

    def create(self, system: str, messages: list, tools: list[dict]) -> Any: ...


@runtime_checkable
class Guardrail(Protocol):
    """Input and output policy checks. Must raise (not allow) when it cannot decide."""

    def check_input(self, text: str) -> Any: ...

    def check_output(self, text: str, grounding: str, query: str) -> Any: ...


class TransactionStore(Protocol):
    def get(self, tenant_id: str, txn_id: str) -> dict | None: ...

    def recent(self, tenant_id: str, limit: int) -> list[dict]:
        """The tenant's newest `limit` records (bounded: a large merchant has millions)."""
        ...


class LogSearch(Protocol):
    def events(self, tenant_id: str, txn_id: str, limit: int) -> list[dict]: ...


class KnowledgeSearch(Protocol):
    def search(self, query: str, k: int = 4, groups: list[str] | None = None, **kw) -> list[dict]: ...


Embedder = Callable[[str], list[float]]


class HealthStore(Protocol):
    """Shared model-health and control-plane state, visible to every worker."""
    clock: Callable[[], float]

    def publish_health(self, name: str, opened_at: float | None, cooldown: float, updated: float) -> None: ...

    def read_health(self, name: str) -> dict | None: ...

    def write_control(self, key: str, value: dict) -> None: ...

    def read_control(self, key: str) -> dict | None: ...


class SessionRepository(HealthStore, Protocol):
    """Durable case record. Same contract for SQLite (finbot/store.py) and
    DynamoDB (finbot/store_dynamo.py); tests/test_store_contract.py runs both."""

    def create(self, session_id: str, tenant_id: str, state: dict) -> None: ...

    def load(self, session_id: str) -> tuple[int, dict]: ...

    def tenant_of(self, session_id: str) -> str: ...

    def claim(self, session_id: str, worker: str, lease_s: float = 30) -> int: ...

    def release(self, session_id: str, worker: str) -> None: ...

    def begin_turn(self, session_id: str, msg_id: str, worker: str, user_text: str) -> dict | None: ...

    def commit_turn(self, session_id: str, msg_id: str, worker: str, expected_version: int,
                    state: dict, answer: str, meta: dict) -> int: ...

    def incomplete_turns(self, session_id: str) -> list[dict]: ...

    def turns(self, session_id: str) -> list[dict]: ...

    def begin_action(self, op_id: str, session_id: str, msg_id: str, kind: str, params: dict) -> str: ...

    def finish_action(self, op_id: str, status: str, result: dict) -> None: ...

    def unknown_actions(self, session_id: str) -> list[dict]: ...

    def close(self) -> None: ...


class MessageSource(Protocol):
    """Work queue feeding the workers (SQS in production, in-memory in tests).
    At-least-once delivery: a message not acked reappears after its visibility timeout."""

    def receive(self, max_messages: int = 10) -> Iterable[QueueMessage]: ...

    def ack(self, msg: QueueMessage) -> None: ...

    def retry_later(self, msg: QueueMessage, delay_s: float) -> None: ...

    def dead_letter(self, msg: QueueMessage, reason: str) -> None: ...


class QueueMessage(Protocol):
    body: dict
    receive_count: int
