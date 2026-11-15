"""Turn pipeline:

  user text
    -> input guardrail (block / mask PII)
    -> focus detection + typed session state         (memory.py)
    -> Claude tool loop (read-only, tenant-scoped)    (tools.py, llm.py)
    -> hallucination gate: verify, one repair, verify (verification.py)
    -> output guardrail (contextual grounding + PII)  (guardrails.py)
    -> answer + audit record

The conversation history is append-only (required for prompt caching and
preserved thinking). Volatile state is injected as a mid-conversation system
message after each user message instead of editing earlier turns.
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .guardrails import BLOCKED_OUTPUT_MSG
from .memory import SessionMemory
from .models import ModelOrchestrator, ModelTarget
from .resilience import GuardrailUnavailable, ServiceUnavailable
from .tools import TXN_ID, Session, ToolBox
from .untrusted import SPOTLIGHT_RULE, ToolResultScreen, leaks_prompt
from .verification import EvidenceVerifier, HallucinationGate, VerifierChain

SYSTEM_PROMPT = """You are a support assistant for a payments and financial-services company. You help merchants, customers and support staff understand transactions, declines, refunds, fees and product behaviour.

How you answer:
- Facts about a specific transaction or account come only from tool results in this conversation. Call get_transaction (and search_transaction_logs when explaining a failure) before stating status, amounts, codes or causes.
- If a tool returns status "not_found", tell the user it was not found for their account and ask them to check the ID. Do not guess, and do not describe what such a transaction "probably" looked like.
- If a tool returns status "error", say the system is temporarily unavailable. Don't invent an answer.
- Product, policy and code-meaning questions: use search_knowledge_base and cite the source URL. If nothing relevant is found, say you don't have documentation on it rather than answering from general knowledge.
- Session state arrives in system messages tagged <session_state>. Only the focus transaction is active. Do not carry details of other transactions into the current answer unless the user explicitly refers back to them by ID.
- You are read-only: you cannot refund, retry, cancel or change anything. If asked, explain the proper channel.
- Out of scope: investment advice, legal or tax advice, topics unrelated to our products. Decline briefly and offer what you can help with.
- Never ask for or repeat full card numbers, CVV, passwords or one-time codes.
- When a transaction record includes "guidance", it is the authoritative retry policy and next step, computed from our rules. Give the user its next_step (it is already written for their role) and, if relevant, what to tell the cardholder (tell_the_cardholder). If retryable is false, never suggest retrying the same payment; only the alternatives in retry_only_if (for example another card, or checking the status first) are allowed. If retryable is true, state the retry_rule including its timing.
- Direct people to whoever can actually fix the problem: the cardholder's bank for issuer declines, the merchant's settings or integration for risk blocks and request errors, support for anything you cannot verify.
- Be concise: lead with the answer, then the reason, then the next step for the user.
""" + SPOTLIGHT_RULE

UNAVAILABLE_MSG = ("I can't reach our systems right now, so I won't guess. Please try again in a minute or two. "
                   "If it's urgent, contact {contact} and quote reference {ref}.")
HANDOFF_MSG = {
    "merchant": ("I still can't verify this from our records, so I've flagged it for a support specialist "
                 "(reference {ref}). You can also contact {contact} and quote that reference."),
    "support": ("This was unverifiable twice in a row: escalate to tier 2 with reference {ref}. "
                "The session record has the full context."),
}


@dataclass
class Turn:
    answer: str
    blocked_by: str | None
    reasons: list[str]
    tools_called: list[dict]
    focus: str | None
    latency_ms: int
    repaired: bool = False  # passed verification only after one repair attempt
    escalated: bool = False  # human handoff offered on this turn
    tier: str | None = None  # routing tier chosen for this turn (None = single chain)
    models_used: list = field(default_factory=list)  # model target per call, shows failovers


@dataclass
class ChatSession:
    session: Session
    memory: SessionMemory = field(default_factory=SessionMemory)
    history: list = field(default_factory=list)
    pending_notice: str | None = None
    # Transient per-turn controls, never persisted (dump_session ignores them):
    heartbeat: Callable[[], object] | None = field(default=None, repr=False, compare=False)  # renews the lease
    deadline: float | None = field(default=None, repr=False, compare=False)
    nonce: str | None = field(default=None, repr=False, compare=False)


class Chatbot:
    def __init__(self, llm, guardrail, toolbox: ToolBox, settings, audit_path: str | None = None):
        self.llm, self.guard, self.toolbox, self.settings = llm, guardrail, toolbox, settings
        self.audit_path = audit_path
        self.sleep = time.sleep  # injectable for tests
        names = set(toolbox.processors.names()) if getattr(toolbox, "processors", None) else None
        self.gate = HallucinationGate(VerifierChain([EvidenceVerifier(names)]),
                                      repair_attempts=settings.repair_attempts)
        self.screen = ToolResultScreen(guardrail)  # third-party text in tool results
        self._wrapped: tuple | None = None

    def engine(self) -> ModelOrchestrator:
        """The model orchestrator. A plain client assigned to .llm (tests, custom
        integrations) is wrapped in a single-target orchestrator so it still gets
        retries and a circuit breaker."""
        if isinstance(self.llm, ModelOrchestrator):
            self.llm.sleep = self.sleep
            return self.llm
        if not self._wrapped or self._wrapped[0] is not self.llm:
            target = ModelTarget(type(self.llm).__name__, self.llm, attempts=self.settings.llm_retry_attempts)
            self._wrapped = (self.llm, ModelOrchestrator([target], sleep=self.sleep,
                                                         breaker_threshold=self.settings.model_breaker_threshold,
                                                         cooldown_s=self.settings.model_breaker_cooldown_s))
        self._wrapped[1].sleep = self.sleep
        return self._wrapped[1]

    def new_session(self, tenant_id: str, groups: list[str], role: str = "support") -> ChatSession:
        return ChatSession(Session(str(uuid.uuid4()), tenant_id, groups, role))

    # ------------------------------------------------------------------ main

    def ask(self, cs: ChatSession, user_text: str) -> Turn:
        """Run one turn. If a dependency is down after retries, roll the turn back
        (so resending the same message is safe) and tell the user what to do."""
        t0 = time.time()
        hist_len, mem, notice = len(cs.history), copy.deepcopy(cs.memory), cs.pending_notice
        try:
            return self._ask(cs, user_text, t0)
        except ServiceUnavailable as e:
            del cs.history[hist_len:]
            cs.memory, cs.pending_notice = mem, notice
            msg = UNAVAILABLE_MSG.format(contact=self.settings.support_contact, ref=self._ref(cs))
            return self._finish(cs, user_text, msg, "unavailable", [str(e)], [], t0)

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # fail closed: no unguarded input or output
            raise GuardrailUnavailable(f"guardrail unavailable: {type(e).__name__}") from e

    def _ask(self, cs: ChatSession, user_text: str, t0: float) -> Turn:
        g_in = self._guard(self.guard.check_input, user_text)
        if g_in.action == "block":
            return self._finish(cs, user_text, g_in.text, "input_guardrail", g_in.reasons, [], t0)
        text = g_in.text  # PII-masked

        focus, changed = cs.memory.update_focus(text)
        if cs.memory.turns_in_thread >= self.settings.max_turns_per_thread:
            cs.history = []  # roll to a fresh thread; typed state carries continuity
            cs.memory.turns_in_thread = 0
        cs.memory.turns_in_thread += 1

        notices = [cs.memory.render()]
        if changed:
            notices.append(f"Focus changed to {focus or 'a general question'}; treat earlier "
                           "transactions as unrelated.")
        if cs.pending_notice:
            notices.append(cs.pending_notice)
            cs.pending_notice = None
        cs.history.append({"role": "user", "content": text})
        cs.history.append({"role": "system", "content": "\n".join(notices)})

        tool_log: list[dict] = []
        run_tool = self.toolbox.handler(cs.session)
        cs.nonce = ToolResultScreen.new_nonce()  # spotlighting delimiter for this turn's tool data
        engine = self.engine()
        cs.deadline = engine.clock() + self.settings.turn_deadline_s
        # Model routing is decided in code: optional cheap tier for simple turns,
        # strong tier for hard ones and for every repair attempt.
        if engine.routing is None:  # engine built without a strategy: use configured one
            from .registry import ROUTING
            engine.routing = ROUTING.create(self.settings.routing)
        tier = engine.route(text, len(set(TXN_ID.findall(text))), cs.memory.consecutive_fallbacks,
                            repairing=False)
        tiered = engine.routing.name == "tiered"
        route: dict[str, Any] = {"tier": tier, "models": []}
        answer = self._loop(cs, run_tool, tool_log, tier, route["models"])

        def regenerate(feedback: str):  # repair always runs on the strong tier
            cs.history.append({"role": "user", "content": feedback})
            retry_log: list[dict] = []
            out = self._loop(cs, run_tool, retry_log, "strong" if tiered else None, route["models"])
            return out, retry_log

        gate = self.gate.run(answer, tool_log, lambda: cs.memory.focus, regenerate)
        tool_log, answer, repaired = gate.tool_log, gate.answer, gate.repaired
        if gate.action == "block":
            cs.pending_notice = ("Your previous answer was withheld by the evidence check "
                                 f"({', '.join(gate.reasons)}); the user saw a fallback message.")
            return self._finish(cs, text, gate.answer, "evidence_guard", gate.reasons, tool_log, t0, route=route)

        if leaks_prompt(answer, SYSTEM_PROMPT):  # canary: an injection that got the model to recite its rules
            cs.pending_notice = "Your previous answer was withheld: it repeated your instructions."
            return self._finish(cs, text, BLOCKED_OUTPUT_MSG, "output_guardrail", ["prompt_leak"], tool_log, t0,
                                route=route)
        grounding = "\n".join(json.dumps(c["result"]) for c in tool_log if c["result"]["status"] == "found")
        g_out = self._guard(self.guard.check_output, answer, grounding, text)
        if g_out.action == "block":
            cs.pending_notice = "Your previous answer was withheld by the output guardrail."
            return self._finish(cs, text, g_out.text, "output_guardrail", g_out.reasons, tool_log, t0, route=route)
        return self._finish(cs, text, g_out.text, None, g_in.reasons + gate.reasons + g_out.reasons,
                            tool_log, t0, repaired, route=route)

    def _loop(self, cs: ChatSession, run_tool, tool_log: list[dict], tier: str | None,
              models_used: list[str]) -> str:
        """Claude tool-use loop. Appends every message to the (append-only) history."""
        for _ in range(self.settings.max_tool_rounds):
            if cs.heartbeat:  # renew the lease before every model call (raises if superseded)
                cs.heartbeat()
            engine = self.engine()
            resp = engine.create(SYSTEM_PROMPT, cs.history, self.toolbox.schemas(), tier=tier,
                                 deadline=cs.deadline)
            models_used.append(engine.last_target or "unknown")
            cs.history.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                answer = "I can't help with that request."
                break
            if resp.stop_reason != "tool_use":
                answer = "".join(b.text for b in resp.content if b.type == "text").strip()
                break
            results = []
            for b in resp.content:
                if b.type != "tool_use":
                    continue
                res = self.screen.sanitize(b.name, run_tool(b.name, dict(b.input)))
                cs.memory.ingest_tool_result(b.name, dict(b.input), res)
                tool_log.append({"name": b.name, "input": dict(b.input), "result": res})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": ToolResultScreen.wrap(res, cs.nonce or ToolResultScreen.new_nonce()),
                                "is_error": res["status"] == "error"})
            cs.history.append({"role": "user", "content": results})
        else:
            answer = BLOCKED_OUTPUT_MSG
        return answer or BLOCKED_OUTPUT_MSG  # never send an empty reply

    # ------------------------------------------------------------------ feedback

    def feedback(self, cs: ChatSession, turn: Turn, rating: str, comment: str = "",
                 queue_path: str = "review_queue.jsonl") -> None:
        """Send graded answers to a HUMAN review queue. Never auto-ingest model
        answers into the knowledge base: that turns one hallucination into a
        permanent 'source' (memory poisoning). Approved fixes go into the source
        docs, then flow back through normal ingestion."""
        with open(queue_path, "a") as f:
            f.write(json.dumps({"session": cs.session.session_id, "rating": rating, "comment": comment,
                                "answer": turn.answer, "focus": turn.focus,
                                "tools": [{"name": c["name"], "status": c["result"]["status"]}
                                          for c in turn.tools_called]}) + "\n")

    # ------------------------------------------------------------------ audit

    @staticmethod
    def _ref(cs: ChatSession) -> str:
        return cs.session.session_id[:8].upper()

    def _finish(self, cs, user_text, answer, blocked_by, reasons, tool_log, t0, repaired=False,
                route: dict | None = None) -> Turn:
        route = route or {"tier": None, "models": []}
        # Direct the user to a human when the bot keeps failing to verify an answer.
        if blocked_by in ("evidence_guard", "output_guardrail"):
            cs.memory.consecutive_fallbacks += 1
        elif blocked_by is None:
            cs.memory.consecutive_fallbacks = 0
        escalated = cs.memory.consecutive_fallbacks >= self.settings.escalate_after_fallbacks
        if escalated:
            tmpl = HANDOFF_MSG["merchant" if cs.session.role == "merchant" else "support"]
            answer = f"{answer} {tmpl.format(ref=self._ref(cs), contact=self.settings.support_contact)}"
            cs.memory.consecutive_fallbacks = 0
        turn = Turn(answer, blocked_by, reasons, tool_log, cs.memory.focus,
                    int((time.time() - t0) * 1000), repaired, escalated, route["tier"], route["models"])
        if self.audit_path:
            with open(self.audit_path, "a") as f:
                f.write(json.dumps({
                    "ts": time.time(), "session": cs.session.session_id, "tenant": cs.session.tenant_id,
                    "user_text_masked": user_text, "blocked_by": blocked_by, "reasons": reasons,
                    "focus": turn.focus, "latency_ms": turn.latency_ms, "repaired": turn.repaired,
                    "escalated": turn.escalated, "tier": turn.tier, "models": turn.models_used,
                    "tools": [{"name": c["name"], "input": c["input"], "status": c["result"]["status"],
                               **({"screened": c["result"]["screened"]} if c["result"].get("screened") else {})}
                              for c in tool_log]}) + "\n")
        return turn
