"""LLM clients behind one interface: create(system, messages, tools) -> response.

  bedrock    Claude on Amazon Bedrock (Messages API via AnthropicBedrockMantle;
             IAM credentials from the default AWS chain)
  anthropic  Claude API, or your internal gateway via ANTHROPIC_BASE_URL
  fake       deterministic scripted model for offline tests and demos

All return objects with .content (blocks with .type/.text/.id/.name/.input)
and .stop_reason, i.e. the Anthropic Messages shape.
"""
from __future__ import annotations

import re
from types import SimpleNamespace as NS


class ClaudeLLM:
    def __init__(self, provider: str, model: str, region: str = "", effort: str = "medium"):
        import anthropic  # lazy: pip install anthropic
        # max_retries=0: the model orchestrator owns retries, breakers and failover
        # (finbot/models.py), instead of stacking SDK retries underneath it.
        if provider == "bedrock":
            self.client = anthropic.AnthropicBedrockMantle(aws_region=region or "us-east-1", max_retries=0)
        else:
            self.client = anthropic.Anthropic(max_retries=0)
        self.model, self.effort = model, effort

    def create(self, system: str, messages: list, tools: list[dict]):
        return self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            # Stable system prompt + tool list first, cached; volatile state goes
            # into messages as a mid-conversation system message.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            output_config={"effort": self.effort},
        )


ROUTING_CUE = re.compile(r"\b(processors?|rout(e|ed|ing)|cascad\w*|fail ?over|acquirer)\b", re.I)
HEALTH_CUE = re.compile(r"\b(down|outage|health|incident|offline|degraded)\b", re.I)


class FakeLLM:
    """Scripted stand-in that behaves like a well-behaved tool-using model."""

    def __init__(self, *_):
        self.calls = 0

    def create(self, system: str, messages: list, tools: list[dict]):
        self.calls += 1
        last = messages[-1]
        # After tool results: compose an answer from them.
        if last["role"] == "user" and isinstance(last["content"], list) and \
                last["content"] and isinstance(last["content"][0], dict) and \
                last["content"][0].get("type") == "tool_result":
            from .untrusted import unwrap
            results = [unwrap(b["content"]) for b in last["content"]]
            return self._answer(results)
        # Find the latest real user text (skip system messages).
        # (verifier feedback is not a new question: redo the latest real one)
        user = next(m for m in reversed(messages) if m["role"] == "user" and isinstance(m["content"], str)
                    and not m["content"].startswith("<verifier>"))
        text = user["content"]
        from .domains import by_id, find_ids
        from .domains.base import _PREFIXES
        focus = re.search(rf"focus_(?:transaction|record): ((?:{'|'.join(_PREFIXES)})_\w+)", "\n".join(
            m["content"] for m in messages if m["role"] == "system" and isinstance(m["content"], str)))
        ids = find_ids(text)
        tid = ids[-1] if ids else (focus.group(1) if focus and re.search(r"\b(it|this|that)\b", text, re.I) else None)
        tool_names = {t["name"] for t in tools}
        processors: list[str] = next((t["input_schema"]["properties"]["processor"]["enum"] for t in tools
                           if t["name"] == "get_processor_health"), [])
        named = [p for p in processors if re.search(rf"\b{p}\b", text, re.I)]
        if named and HEALTH_CUE.search(text):
            return NS(stop_reason="tool_use", content=[
                NS(type="tool_use", id=f"t{self.calls}h", name="get_processor_health", input={"processor": named[0]})])
        domain = by_id(tid)
        if domain is not None and domain.tool in tool_names:
            return NS(stop_reason="tool_use", content=[
                NS(type="tool_use", id=f"t{self.calls}d", name=domain.tool, input={domain.id_arg: tid})])
        if tid and "get_transaction" in tool_names:
            calls = [NS(type="tool_use", id=f"t{self.calls}a", name="get_transaction", input={"txn_id": tid}),
                     NS(type="tool_use", id=f"t{self.calls}b", name="search_transaction_logs",
                        input={"txn_id": tid, "limit": 10})]
            if "get_routing_trace" in tool_names and ROUTING_CUE.search(text):
                calls.append(NS(type="tool_use", id=f"t{self.calls}r", name="get_routing_trace",
                                input={"txn_id": tid}))
            return NS(stop_reason="tool_use", content=calls)
        return NS(stop_reason="tool_use", content=[
            NS(type="tool_use", id=f"t{self.calls}k", name="search_knowledge_base", input={"query": text})])

    def _answer(self, results: list[dict]):
        from .domains import DOMAINS
        parts: list[str] = []
        for r in results:
            if r["status"] == "error":
                if not any("temporarily unavailable" in p for p in parts):
                    parts.append("That system is temporarily unavailable, so I can't check right now. "
                                 "Please try again in a few minutes.")
                continue
            domain = next((d for d in DOMAINS.values() if d.source == r["source"]), None)
            if domain is not None:
                parts.append(self._domain(domain, r))
            elif r["source"] == "txn_db":
                if r["status"] == "not_found":
                    parts.append("I could not find that transaction for your account.")
                else:
                    d = r["data"]
                    s = f"Transaction {d['txn_id']} was {d['status']} for {d['amount']} {d['currency']}"
                    if d.get("decline_code"):
                        s += f" with decline code {d['decline_code']} ({d['processor_response']})"
                    if d.get("error"):
                        s += f" with gateway error {d['error']}"
                    if d.get("guidance"):
                        g = d["guidance"]
                        s += f". Next step: {g['next_step']} Retry policy: {g['retry_rule']}"
                    parts.append(s + ".")
            elif r["source"] == "routing" and r["status"] == "found":
                parts.append(self._routing(r["data"]))
            elif r["source"] == "processor_health" and r["status"] == "found":
                parts.append(self._health(r["data"]))
            elif r["source"] == "knowledge_base":
                if r["status"] == "not_found":
                    parts.append("I don't have documentation on that, so I won't guess.")
                else:
                    h = r["data"][0]
                    parts.append(f"{h['text']} (source: {h['source_url']})")
        return NS(stop_reason="end_turn", content=[NS(type="text", text=" ".join(parts))])

    @staticmethod
    def _domain(domain, r: dict) -> str:
        if r["status"] == "not_found":
            return f"I could not find that {domain.noun} for your account."
        d = r["data"]
        s = domain.summary(d) or f"I have the {domain.noun} record but can't read its details."
        g = d.get("guidance") or {}
        if g:
            s += f" {g['meaning']} Next step: {g['next_step']}"
            if g.get("retry_rule"):
                s += f" Policy: {g['retry_rule']}"
        return s

    @staticmethod
    def _routing(d: dict) -> str:
        advice = d.get("cascade_advice", {})
        if not d.get("routed"):
            return f"It never reached a processor: {advice.get('reason', '')}"
        steps = []
        for a in d["attempts"]:
            s = f"attempt {a['n']} on {a['processor']} returned code {a['code']}"
            if a.get("cascaded"):
                s += f", so it cascaded to the next processor ({a['cascade_reason']})"
            steps.append(s)
        out = "Routing: " + "; ".join(steps) + "."
        if advice.get("final_code") == "00":
            return out
        verdict = ("Another processor may help: " if advice.get("another_processor_would_help")
                   else "Sending it to another processor would not help: ")
        return out + " " + verdict + advice.get("reason", "")

    @staticmethod
    def _health(d: dict) -> str:
        inc = d["incidents"]
        if inc:
            s = f"{d['processor']} has {len(inc)} recorded incident(s): " + "; ".join(
                f"{i['kind']} from {i['start']} to {i['end']} ({i['summary']})" for i in inc) + "."
        else:
            s = f"There are no recorded incidents for {d['processor']}."
        return s + (f" On your account it handled {d['your_attempts']} attempts with "
                    f"{d['your_processor_errors']} processor errors.")


def make_client(provider: str, model: str, region: str = "", effort: str = "medium"):
    """Factory (delegates to the LLM provider registry in finbot/registry.py)."""
    from .registry import LLM_PROVIDERS
    return LLM_PROVIDERS.create(provider, model=model, region=region, effort=effort)


def make_engine(settings):
    """Build the model orchestrator from settings.

    FINBOT_MODEL_CHAIN  ordered failover chain, e.g.
        "bedrock:anthropic.claude-opus-5-5@us-east-1,bedrock:anthropic.claude-opus-5-5@us-west-2,anthropic:claude-opus-5-5"
    FINBOT_ROUTING=tiered + FINBOT_FAST_MODEL  route simple turns to a faster model, e.g.
        "bedrock:anthropic.claude-sonnet-5-5@us-east-1"
    Without a chain: a single target from FINBOT_LLM_PROVIDER / FINBOT_MODEL.
    """
    from .models import ModelOrchestrator, parse_chain
    from .registry import ROUTING

    def mk(provider, model, region):
        return make_client(provider, model, region or settings.aws_region, settings.effort)

    spec = settings.model_chain or (
        f"{settings.llm_provider}:{settings.resolved_model()}"
        + (f"@{settings.aws_region}" if settings.llm_provider == "bedrock" else ""))
    strong = parse_chain(spec, mk, "strong")
    fast = parse_chain(settings.fast_model, mk, "fast") if settings.routing == "tiered" else []
    for t in strong + fast:
        t.attempts = settings.llm_retry_attempts
    return ModelOrchestrator(strong, fast, breaker_threshold=settings.model_breaker_threshold,
                             cooldown_s=settings.model_breaker_cooldown_s,
                             routing=ROUTING.create(settings.routing))
