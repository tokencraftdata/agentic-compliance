"""Turn pipeline:

  user text
    -> session risk lock, input guardrail (block / mask PII)
    -> learned guard policy: block, or escalate to a person (finbot/adaptive)
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

import contextvars
import copy
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .actions import ACTION_WORDS, WriteRequest
from .actions import detect as detect_write_request
from .adaptive.policy import BASELINE, GuardPolicy
from .adaptive.risk import next_score
from .adaptive.source import PolicySource, StaticPolicySource
from .guardrails import BLOCKED_INPUT_MSG, BLOCKED_OUTPUT_MSG, mask_pii
from .memory import SessionMemory
from .models import ModelOrchestrator, ModelTarget
from .resilience import GuardrailUnavailable, ServiceUnavailable
from .tickets import InMemoryTickets, TicketStore, open_ticket
from .tools import TXN_ID, Session, ToolBox
from .untrusted import SPOTLIGHT_RULE, ToolResultScreen, leaks_prompt
from .verification import EvidenceVerifier, HallucinationGate, PolicyVerifier, TicketVerifier, VerifierChain

SYSTEM_PROMPT = """You are a support assistant for a payments and financial-services company. You help merchants, customers and support staff understand transactions, declines, refunds, fees and product behaviour.

How you answer:
- Facts about a specific transaction or account come only from tool results in this conversation. Call get_transaction (and search_transaction_logs when explaining a failure) before stating status, amounts, codes or causes.
- If a tool returns status "not_found", tell the user it was not found for their account and ask them to check the ID. Do not guess, and do not describe what such a transaction "probably" looked like.
- If a tool returns status "error", say the system is temporarily unavailable. Don't invent an answer.
- Product, policy and code-meaning questions: use search_knowledge_base and cite the source URL. If nothing relevant is found, say you don't have documentation on it rather than answering from general knowledge.
- Session state arrives in system messages tagged <session_state>. Only the focus transaction is active. Do not carry details of other transactions into the current answer unless the user explicitly refers back to them by ID.
- You are read-only: you cannot refund, retry, cancel or change anything. Requests to change something are turned into tickets by the system, not by you. Never say you raised, created or opened a ticket; mention a ticket id only if <session_state> lists it.
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
ESCALATION_MSG = {
    "merchant": ("This needs a person rather than a bot, so I've passed it to a support specialist "
                 "(reference {ref}). You can also contact {contact} and quote that reference."),
    "support": "Route this to a human: it matches the escalation policy ({why}). Reference {ref}.",
}
RISK_MSG = ("I've paused this conversation and passed it to a support specialist (reference {ref}). "
            "Please contact {contact} and quote that reference.")

TICKET_MSG = {
    "merchant": ("I can't make changes myself, so I've opened ticket {id} asking our team to {action} {target}. "
                 "A person will review it and carry it out; nothing has been changed yet. "
                 "Quote {id} if you contact {contact}."),
    "support": "Ticket {id} opened for manual execution: {kind} on {target}. Nothing has been executed.",
}
TICKET_EXISTS = " This request was already open as {id}, so no duplicate was created."
TICKET_NOT_FOUND = ("I couldn't find {txn} for your account, so I haven't opened a ticket. "
                    "Please check the transaction ID.")
CARD_NOTICE = ("I removed card details from your message. Please don't share card numbers, security codes "
               "or PINs here: we never need them.")
CARD_REASONS = {"pii:card", "pii:card_like", "pii:track_data", "pii:cvv", "pii:expiry", "pii:pin"}

# Per-turn context (contextvars: safe if one Chatbot serves turns on several threads).
_POLICY: contextvars.ContextVar[GuardPolicy] = contextvars.ContextVar("guard_policy", default=BASELINE)
_TICKETS: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar("known_tickets", default=frozenset())
_CARD_SEEN: contextvars.ContextVar[bool] = contextvars.ContextVar("card_seen", default=False)



def _mask_values(value: Any) -> Any:
    """mask_pii over every string in a JSON-like value (tool inputs in the audit log)."""
    if isinstance(value, str):
        return mask_pii(value)[0]
    if isinstance(value, dict):
        return {k: _mask_values(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_values(v) for v in value]
    return value


def _mask_blocks(content: list) -> list:
    """Mask card data in the model's text blocks before they enter history (persisted and
    replayed). Other blocks (tool_use, thinking with signatures) are kept untouched."""
    out = []
    for b in content:
        if getattr(b, "type", None) == "text" and mask_pii(b.text)[1]:
            masked = mask_pii(b.text)[0]
            b = b.model_copy(update={"text": masked}) if hasattr(b, "model_copy") else \
                type(b)(**{**vars(b), "text": masked})
        out.append(b)
    return out


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
    question: str = ""  # the user's text as the model saw it (PII-masked)
    policy_version: int | None = None  # guard policy version that served the turn


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
    def __init__(self, llm, guardrail, toolbox: ToolBox, settings, audit_path: str | None = None,
                 policies: PolicySource | None = None, tickets: TicketStore | None = None):
        self.llm, self.guard, self.toolbox, self.settings = llm, guardrail, toolbox, settings
        self.audit_path = audit_path
        self.policies: PolicySource = policies or StaticPolicySource()
        self.tickets: TicketStore = tickets if tickets is not None else InMemoryTickets()
        self.sleep = time.sleep  # injectable for tests
        names = set(toolbox.processors.names()) if getattr(toolbox, "processors", None) else None
        self.gate = HallucinationGate(VerifierChain([EvidenceVerifier(names), PolicyVerifier(_POLICY.get),
                                                     TicketVerifier(lambda: set(_TICKETS.get()))]),
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
        token = _POLICY.set(self.policies.for_session(cs.session.session_id))
        tokens: list[contextvars.Token] = [_TICKETS.set(frozenset(t["id"] for t in cs.memory.tickets))]
        try:
            # Card data is masked before anything else sees the text: guards, the model,
            # history, audit, review queue and shadow logs, on blocked paths too.
            user_text, premasked = mask_pii(user_text)
            tokens.append(_CARD_SEEN.set(bool(CARD_REASONS & set(premasked))))
            return self._ask(cs, user_text, t0, premasked)
        except ServiceUnavailable as e:
            del cs.history[hist_len:]
            cs.memory, cs.pending_notice = mem, notice
            msg = UNAVAILABLE_MSG.format(contact=self.settings.support_contact, ref=self._ref(cs))
            return self._finish(cs, user_text, msg, "unavailable", [str(e)], [], t0)
        finally:
            for tok in reversed(tokens):
                tok.var.reset(tok)
            _POLICY.reset(token)

    def _guard(self, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # fail closed: no unguarded input or output
            raise GuardrailUnavailable(f"guardrail unavailable: {type(e).__name__}") from e

    def _ask(self, cs: ChatSession, user_text: str, t0: float, premasked: list[str] | None = None) -> Turn:
        policy = _POLICY.get()
        if cs.memory.risk >= policy.risk_threshold:  # locked: a person takes it from here
            msg = RISK_MSG.format(ref=self._ref(cs), contact=self.settings.support_contact)
            return self._finish(cs, user_text, msg, "session_risk", [f"risk:{cs.memory.risk:g}"], [], t0,
                                escalated=True)
        g_in = self._guard(self.guard.check_input, user_text)
        g_in.reasons = list(dict.fromkeys([*(premasked or []), *g_in.reasons]))
        if g_in.action == "block":
            if any(r.startswith("prompt_attack") for r in g_in.reasons):  # off-topic is not an attack
                cs.memory.risk = next_score(cs.memory.risk, attack=True, text=user_text)
            return self._finish(cs, user_text, g_in.text, "input_guardrail", g_in.reasons, [], t0)
        text = g_in.text  # PII-masked

        learned = policy.input_decision(text)
        if learned.action == "block":
            cs.memory.risk = next_score(cs.memory.risk, attack=True, text=text)
            return self._finish(cs, text, BLOCKED_INPUT_MSG, "input_guardrail", list(learned.reasons), [], t0)
        if learned.action == "escalate":
            tmpl = ESCALATION_MSG["merchant" if cs.session.role == "merchant" else "support"]
            msg = tmpl.format(ref=self._ref(cs), contact=self.settings.support_contact,
                              why=", ".join(learned.reasons))
            return self._finish(cs, text, msg, "escalation", list(learned.reasons), [], t0, escalated=True)
        cs.memory.risk = next_score(cs.memory.risk, attack=False, text=text)

        request = detect_write_request(text, cs.memory.focus)
        if request:   # decided in code, before any model call: read-only bot, a person executes
            return self._ticket_turn(cs, text, request, t0)

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

        answer = mask_pii(answer)[0]  # local pass on every provider; Bedrock's anonymizer is a second layer
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
            cs.history.append({"role": "assistant", "content": _mask_blocks(resp.content)})
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
                                "question": turn.question, "answer": turn.answer, "focus": turn.focus,
                                "policy": turn.policy_version,
                                "tools": [{"name": c["name"], "status": c["result"]["status"]}
                                          for c in turn.tools_called]}) + "\n")

    # ------------------------------------------------------------------ audit

    @staticmethod
    def _ref(cs: ChatSession) -> str:
        return cs.session.session_id[:8].upper()

    def _finish(self, cs, user_text, answer, blocked_by, reasons, tool_log, t0, repaired=False,
                route: dict | None = None, escalated: bool = False) -> Turn:
        route = route or {"tier": None, "models": []}
        # Direct the user to a human when the bot keeps failing to verify an answer.
        if blocked_by in ("evidence_guard", "output_guardrail"):
            cs.memory.consecutive_fallbacks += 1
        elif blocked_by is None:
            cs.memory.consecutive_fallbacks = 0
        if cs.memory.consecutive_fallbacks >= self.settings.escalate_after_fallbacks:
            escalated = True
            tmpl = HANDOFF_MSG["merchant" if cs.session.role == "merchant" else "support"]
            answer = f"{answer} {tmpl.format(ref=self._ref(cs), contact=self.settings.support_contact)}"
            cs.memory.consecutive_fallbacks = 0
        if _CARD_SEEN.get():
            answer = f"{CARD_NOTICE}\n\n{answer}"
        policy = _POLICY.get()
        turn = Turn(answer, blocked_by, reasons, tool_log, cs.memory.focus,
                    int((time.time() - t0) * 1000), repaired, escalated, route["tier"], route["models"],
                    user_text, policy.version)
        shadow = self._shadow(policy, user_text, answer if blocked_by is None else "")
        if self.audit_path:
            with open(self.audit_path, "a") as f:
                f.write(json.dumps({
                    "ts": time.time(), "session": cs.session.session_id, "tenant": cs.session.tenant_id,
                    "user_text_masked": user_text, "blocked_by": blocked_by, "reasons": reasons,
                    "focus": turn.focus, "latency_ms": turn.latency_ms, "repaired": turn.repaired,
                    "escalated": turn.escalated, "tier": turn.tier, "models": turn.models_used,
                    "policy": turn.policy_version, "risk": cs.memory.risk, **({"shadow": shadow} if shadow else {}),
                    "tools": [{"name": c["name"], "input": _mask_values(c["input"]), "status": c["result"]["status"],
                               **({"screened": c["result"]["screened"]} if c["result"].get("screened") else {})}
                              for c in tool_log]}) + "\n")
        return turn

    def _ticket_turn(self, cs: ChatSession, text: str, req: WriteRequest, t0: float) -> Turn:
        """Open a ticket for a person. Verifies the transaction belongs to this tenant first,
        records the facts and retry policy at request time, and replies with a fixed template."""
        tool_log: list[dict] = []
        facts: dict = {}
        policy_note = ""
        if req.txn_id:
            args = {"txn_id": req.txn_id}
            res = self.screen.sanitize("get_transaction", self.toolbox.handler(cs.session)("get_transaction", args))
            tool_log.append({"name": "get_transaction", "input": args, "result": res})
            if res["status"] == "error":
                raise ServiceUnavailable("transaction lookup unavailable; no ticket opened")
            cs.memory.ingest_tool_result("get_transaction", args, res)
            if res["status"] == "not_found":
                return self._finish(cs, text, TICKET_NOT_FOUND.format(txn=req.txn_id), "write_request",
                                    [f"write_request:{req.kind}", "txn_not_found"], tool_log, t0)
            data = res.get("data") or {}
            facts = {k: data[k] for k in ("status", "amount", "currency", "decline_code", "error") if k in data}
            g = data.get("guidance") or {}
            if req.kind == "retry" and g.get("retryable") is False:
                policy_note = (f"Retry policy: {g.get('reason_key')} is not retryable; "
                               f"allowed only: {', '.join(g.get('retry_only_if', [])) or 'none'}")
        ticket, created = open_ticket(self.tickets, tenant_id=cs.session.tenant_id,
                                      session_id=cs.session.session_id, role=cs.session.role, kind=req.kind,
                                      txn_id=req.txn_id, text=text, facts=facts, policy_note=policy_note)
        if not any(t["id"] == ticket.id for t in cs.memory.tickets):
            cs.memory.tickets.append({"id": ticket.id, "kind": ticket.kind, "txn_id": ticket.txn_id})
        role = "merchant" if cs.session.role == "merchant" else "support"
        answer = TICKET_MSG[role].format(id=ticket.id, action=ACTION_WORDS.get(req.kind, "act on"),
                                         kind=req.kind, target=req.txn_id or "your account",
                                         contact=self.settings.support_contact)
        if policy_note and role == "merchant":
            answer += " Note: our retry policy says this payment shouldn't be retried as is; the team will check."
        elif policy_note:
            answer += f" {policy_note}."
        if not created:
            answer += TICKET_EXISTS.format(id=ticket.id)
        return self._finish(cs, text, answer, None, [f"write_request:{req.kind}", f"ticket:{ticket.id}"],
                            tool_log, t0, escalated=True)

    def _shadow(self, live: GuardPolicy, user_text: str, answer: str) -> dict | None:
        """Evaluate the shadow policy on this turn without enforcing it. The audit
        record says what it would have done and whether that differs from live."""
        shadow = self.policies.shadow()
        if shadow is None or shadow.version == live.version:
            return None
        s_in, l_in = shadow.input_decision(user_text), live.input_decision(user_text)
        s_out = shadow.answer_violations(answer) if answer else []
        l_out = live.answer_violations(answer) if answer else []
        return {"version": shadow.version, "input": s_in.action, "reasons": [*s_in.reasons, *s_out],
                "diverged": s_in.action != l_in.action or bool(s_out) != bool(l_out)}
