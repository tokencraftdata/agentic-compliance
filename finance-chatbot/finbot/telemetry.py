"""AI observability: traces of every turn, model call, tool call and verification.

Span names and attributes follow the OpenTelemetry semantic conventions for generative AI
(`gen_ai.*`), so any OTel backend (AWS X-Ray / CloudWatch Application Signals, Grafana Tempo,
Datadog, Honeycomb, Langfuse, Arize Phoenix) shows them as LLM traces without custom parsing.

    finbot.turn                      one user message
      ├─ chat <model>                one model call: provider, model, tokens, finish reason, failover
      ├─ execute_tool <tool>         one tool call: status, refused (provenance or scope), screened
      └─ finbot.verify               the hallucination gate: allow/block, reason codes, repaired,
                                     records_only

What a span never holds: the user's text, the model's answer, tool data, ids of records, or
anything else a customer wrote. Reasons are cut to their code ("status_mismatch",
"ungrounded_amount", "unverified_contact"), because the detail after the colon can hold an
amount, a URL or a record id; codes and counts are enough to see *why* answers are blocked. The
session id is hashed. This keeps traces out of PCI and privacy scope.

Backends (FINBOT_TELEMETRY):
    none   no spans (default)
    log    one JSON line per finished span on stdout, or to FINBOT_TELEMETRY_PATH
    otel   the OpenTelemetry API (`pip install opentelemetry-sdk opentelemetry-exporter-otlp`);
           exporter, endpoint and sampling come from the standard OTEL_* environment variables
    memory spans kept in a list (tests)
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any

# Attributes a span may carry. Anything else is dropped, so a careless caller cannot leak text.
ALLOWED = frozenset({
    "gen_ai.operation.name", "gen_ai.provider.name", "gen_ai.request.model", "gen_ai.response.model",
    "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens", "gen_ai.response.finish_reasons",
    "gen_ai.tool.name", "gen_ai.conversation.id",
    "finbot.tenant", "finbot.role", "finbot.blocked_by", "finbot.reasons", "finbot.repaired",
    "finbot.records_only", "finbot.escalated", "finbot.tier", "finbot.policy_version",
    "finbot.failover", "finbot.tool.status", "finbot.tool.refused_by", "finbot.tool.screened",
    "finbot.verify.action", "finbot.verify.first_reasons", "finbot.tool_calls", "finbot.model_calls",
    "error.type",
})


def codes(reasons) -> list[str]:
    """Reason codes only: the part before the first colon, which never holds a value."""
    return sorted({str(r).split(":", 1)[0] for r in reasons})


def session_ref(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]


class Span:
    def __init__(self, name: str, trace_id: str, parent: str | None):
        self.name, self.trace_id, self.parent = name, trace_id, parent
        self.span_id = uuid.uuid4().hex[:16]
        self.attrs: dict[str, Any] = {}
        self.start = time.time()
        self.end: float | None = None
        self.status = "ok"

    def set(self, **attrs: Any) -> None:
        for k, v in attrs.items():
            key = k.replace("__", ".")
            if key in ALLOWED and v is not None:
                self.attrs[key] = list(v) if isinstance(v, (tuple, set, frozenset)) else v

    def as_dict(self) -> dict:
        return {"name": self.name, "trace_id": self.trace_id, "span_id": self.span_id, "parent": self.parent,
                "start": self.start, "duration_ms": round(((self.end or time.time()) - self.start) * 1000, 2),
                "status": self.status, "attributes": self.attrs}


class Telemetry:
    """Tracer with a thread-local span stack. `span()` is a context manager; attributes are set
    with `Span.set(gen_ai__request__model=...)` (double underscores become dots)."""

    def __init__(self, kind: str = "none", path: str = ""):
        self.kind, self.path = kind, path
        self.finished: list[dict] = []            # memory backend
        self._local = threading.local()
        self._lock = threading.Lock()
        self._otel = None
        if kind == "otel":
            from opentelemetry import trace  # optional dependency
            self._otel = trace.get_tracer("finbot")
        elif kind not in ("none", "log", "memory"):
            raise ValueError(f"unknown telemetry backend {kind!r}; known: log, memory, none, otel")

    @property
    def enabled(self) -> bool:
        return self.kind != "none"

    def _stack(self) -> list[Span]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        stack = self._stack()
        parent = stack[-1] if stack else None
        sp = Span(name, parent.trace_id if parent else uuid.uuid4().hex, parent.span_id if parent else None)
        sp.set(**attrs)
        if not self.enabled:
            yield sp
            return
        stack.append(sp)
        otel_cm = self._otel.start_as_current_span(name) if self._otel else contextlib.nullcontext()
        with otel_cm as otel_span:
            try:
                yield sp
            except BaseException as e:
                sp.status = "error"
                sp.set(error__type=type(e).__name__)
                raise
            finally:
                sp.end = time.time()
                stack.pop()
                if otel_span is not None:
                    for k, v in sp.attrs.items():
                        otel_span.set_attribute(k, v)
                self._emit(sp)

    def _emit(self, sp: Span) -> None:
        if self.kind == "memory":
            with self._lock:
                self.finished.append(sp.as_dict())
        elif self.kind == "log":
            line = json.dumps({"stream": "span", **sp.as_dict()}, default=str) + "\n"
            with self._lock:
                if self.path:
                    with open(self.path, "a") as f:
                        f.write(line)
                else:
                    sys.stdout.write(line)


NOOP = Telemetry("none")


def provider_of(target: str | None) -> str | None:
    """'bedrock:anthropic.claude-...@us-east-1' -> 'aws.bedrock'; 'anthropic:...' -> 'anthropic'."""
    if not target:
        return None
    kind = target.split(":", 1)[0]
    return {"bedrock": "aws.bedrock", "anthropic": "anthropic"}.get(kind, kind)


def model_of(target: str | None) -> str | None:
    if not target or ":" not in target:
        return target
    return target.split(":", 1)[1].split("@", 1)[0]
