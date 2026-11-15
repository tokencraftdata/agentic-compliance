"""Three guardrail layers, applied in this order on every turn.

1. Input guardrail   (Bedrock ApplyGuardrail source=INPUT, or local fallback)
   - prompt-attack / jailbreak filter, PII masking (card numbers, SSN, keys),
     denied topics (investment advice, unrelated chit-chat).
2. Evidence guard    (deterministic, ours; the MemoryKeeper lesson)
   - every transaction fact in the answer must come from a tool result
     for the transaction in focus and the session's tenant; if a tool said
     not_found the answer must say so instead of guessing.
3. Output guardrail  (Bedrock ApplyGuardrail source=OUTPUT with contextual
   grounding: tool results are passed as grounding_source and the user question
   as query, so ungrounded / irrelevant answers are blocked), plus PII masking.

ApplyGuardrail is model-independent, so the same policy covers Claude on
Bedrock, Claude via the direct API, or any other LLM behind the gateway.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

BLOCKED_INPUT_MSG = ("I can help with payments, transactions, and account questions. "
                     "I can't help with that request.")
BLOCKED_OUTPUT_MSG = ("I couldn't verify an answer from our records, so I won't guess. "
                      "Please share the transaction ID or contact support.")


@dataclass
class GuardResult:
    action: str               # "allow" | "block" | "masked"
    text: str                 # possibly masked text
    reasons: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ local layer

CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
AWS_KEY = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")
CVV = re.compile(r"\b(cvv|cvc|security code)\s*[:=]?\s*\d{3,4}\b", re.I)
INJECTION = re.compile(
    r"(ignore (all|any|previous|prior) (instructions|rules)|you are now|system prompt|"
    r"developer mode|act as (an? )?(admin|root)|reveal (your|the) (prompt|instructions)|"
    r"tenant_id\s*=|another merchant'?s)", re.I)
DENIED_TOPICS = {
    "investment_advice": re.compile(r"\b(should i (buy|sell|invest)|which stocks?|crypto(currency)? to buy|"
                                    r"stock tips?|price target|(a )?good investment|worth investing|"
                                    r"investment advice|(buy|sell) (shares|stocks?)|"
                                    r"invest in (stocks?|shares|crypto))\b",
                                    re.I),
}


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits][::-1]
    total = sum(d[0::2]) + sum(sum(divmod(2 * x, 10)) for x in d[1::2])
    return total % 10 == 0


def mask_pii(text: str) -> tuple[str, list[str]]:
    reasons = []

    def card(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            reasons.append("pii:card")
            return f"[CARD ••••{digits[-4:]}]"
        return m.group(0)

    text = CARD.sub(card, text)
    for pat, tag in ((SSN, "pii:ssn"), (AWS_KEY, "secret:aws_key"), (CVV, "pii:cvv")):
        if pat.search(text):
            reasons.append(tag)
            text = pat.sub("[REDACTED]", text)
    return text, reasons


class LocalGuardrail:
    """Offline fallback with the same interface as BedrockGuardrail."""

    def check_input(self, text: str) -> GuardResult:
        from .untrusted import USER_INPUT_CATEGORIES, screen_text
        hits = screen_text(text, USER_INPUT_CATEGORIES)
        if INJECTION.search(text) or hits:
            return GuardResult("block", BLOCKED_INPUT_MSG, ["prompt_attack", *hits])
        for name, pat in DENIED_TOPICS.items():
            if pat.search(text):
                return GuardResult("block", BLOCKED_INPUT_MSG, [f"denied_topic:{name}"])
        masked, reasons = mask_pii(text)
        return GuardResult("masked" if reasons else "allow", masked, reasons)

    def check_output(self, text: str, grounding: str, query: str) -> GuardResult:
        masked, reasons = mask_pii(text)
        return GuardResult("masked" if reasons else "allow", masked, reasons)

    def check_untrusted(self, text: str) -> GuardResult:
        """Third-party content (retrieved docs, record fields) about to reach the model."""
        from .untrusted import screen_text
        hits = screen_text(text)
        return GuardResult("block" if hits else "allow", text, hits)


# ------------------------------------------------------------------ Bedrock layer

class BedrockGuardrail:
    """Amazon Bedrock Guardrails via the standalone ApplyGuardrail API.

    Create the guardrail once with infra/create_guardrail.py (denied topics,
    PROMPT_ATTACK filter, PII anonymize/block, contextual grounding thresholds).
    """

    def __init__(self, guardrail_id: str, version: str, region: str):
        import boto3  # lazy
        from botocore.config import Config
        # botocore adaptive retries handle throttling; if it still fails, the
        # orchestrator fails CLOSED (no unguarded input or output gets through).
        self.client = boto3.client("bedrock-runtime", region_name=region,
                                   config=Config(retries={"mode": "adaptive", "max_attempts": 4},
                                                 read_timeout=10, connect_timeout=3))
        self.gid, self.version = guardrail_id, version

    def _apply(self, source: str, content: list[dict]) -> GuardResult:
        resp = self.client.apply_guardrail(guardrailIdentifier=self.gid, guardrailVersion=self.version,
                                           source=source, content=content)
        reasons = []
        for a in resp.get("assessments", []):
            for t in a.get("topicPolicy", {}).get("topics", []):
                reasons.append(f"denied_topic:{t.get('name')}")
            for f in a.get("contentPolicy", {}).get("filters", []):
                reasons.append(f"content:{f.get('type')}")
            for p in a.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
                reasons.append(f"pii:{p.get('type')}:{p.get('action')}")
            for g in a.get("contextualGroundingPolicy", {}).get("filters", []):
                if g.get("action") == "BLOCKED":
                    reasons.append(f"grounding:{g.get('type')}:{g.get('score')}")
        outputs = resp.get("outputs") or []
        text = outputs[0]["text"] if outputs else None
        if resp.get("action") == "GUARDRAIL_INTERVENED":
            blocked = any(r.startswith(("denied_topic", "content", "grounding")) or r.endswith(":BLOCKED")
                          for r in reasons)
            if blocked:
                fallback = BLOCKED_INPUT_MSG if source == "INPUT" else BLOCKED_OUTPUT_MSG
                return GuardResult("block", text or fallback, reasons)
            return GuardResult("masked", text or "", reasons)  # PII anonymized only
        return GuardResult("allow", content[-1]["text"]["text"], reasons)

    def check_input(self, text: str) -> GuardResult:
        return self._apply("INPUT", [{"text": {"text": text}}])

    def check_untrusted(self, text: str) -> GuardResult:
        """Prompt-attack filter on third-party content (retrieved chunks) at query time.
        Only PROMPT_ATTACK counts here: a policy page may legitimately mention a denied topic."""
        r = self._apply("INPUT", [{"text": {"text": text[:20000], "qualifiers": ["guard_content"]}}])
        flags = [x for x in r.reasons if "PROMPT_ATTACK" in x]
        return GuardResult("block" if flags else "allow", text, flags)

    def check_output(self, text: str, grounding: str, query: str) -> GuardResult:
        content = []
        if grounding:  # contextual grounding check needs both qualifiers
            content.append({"text": {"text": grounding[:90000], "qualifiers": ["grounding_source"]}})
            content.append({"text": {"text": query, "qualifiers": ["query"]}})
        content.append({"text": {"text": text, "qualifiers": ["guard_content"]}})
        return self._apply("OUTPUT", content)


# ------------------------------------------------------------------ evidence guard

AMOUNT = re.compile(r"\b\d+\.\d{2}\b")
CODE = re.compile(r"\b(?:code|decline code|response code)\s*[\"']?(\w{2,4})\b", re.I)
URL = re.compile(r"https?://[^\s)\]>\"']+")
POSITIVE = re.compile(r"\b(approved|succeeded|successful|went through|was completed)\b", re.I)
NEGATIVE = re.compile(r"\b(declined|failed|rejected|unsuccessful|did not go through|didn't go through|blocked)\b", re.I)
STATUS_CLAIM = re.compile(r"\b(it|the (payment|transaction|charge))\s+(was|has been|is)\s+"
                          r"(approved|declined|failed|refunded|settled|rejected|blocked)\b", re.I)
NEGATIVE_STATUSES = {"declined", "failed"}
# Lifecycle claims that need a matching field (or doc) in this turn's evidence.
RECORD_CLAIMS = [
    (re.compile(r"\brefund(ed)?\b", re.I), "refund"),
    (re.compile(r"\b(chargeback|disputed?)\b", re.I), "dispute"),
    (re.compile(r"\b(settled|settlement|payout)\b", re.I), "settle"),
]


NEGATION = re.compile(r"(\b(not|never|no|neither|nor|avoid|stop)\b|n't)\W+(\w+\W+){0,2}$", re.I)
# "retry" as advice, not as a noun ("retry policy", "retry rule", "retry limit").
RETRY_ADVICE = re.compile(r"\b(retry(?!\s+(policy|rule|rules|limit|limits|logic|count|window|attempts?)\b)|"
                          r"retrying|re-try|try (it |the payment |the charge )?again|resubmit|"
                          r"charge (it |the card )?again|run it again)\b", re.I)
RETRY_NOW = re.compile(r"\b(retry|try (it )?again)\b[^.]{0,25}\b(now|immediately|right away|straight away)\b|"
                       r"\bkeep (retrying|trying)\b", re.I)


def retry_advice_check(answer: str, tool_log: list[dict]) -> list[str]:
    """Block retry advice that contradicts the code-computed policy (policy.py)."""
    reasons = []
    # Judge each sentence on its own: "use another card" in one sentence must not
    # excuse "you can retry it right away" in the next.
    sentences = _sentences(answer)
    for call in tool_log:
        res = call["result"]
        if call["name"] != "get_transaction" or res["status"] != "found":
            continue
        g = res["data"].get("guidance")
        if not g:
            continue
        allowed = g.get("retry_only_if", [])
        if not g["retryable"] and any(_affirmed(RETRY_ADVICE, s) and not any(q in s.lower() for q in allowed)
                                      for s in sentences):
            reasons.append(f"retry_advice_contradicts_policy:{g['reason_key']}")
        if g["retryable"] and "1 hour" in g["retry_rule"] and RETRY_NOW.search(answer):
            reasons.append(f"retry_timing_violates_policy:{g['reason_key']}")
    # "Retry immediately" is never our policy, with or without a lookup (it can come from a
    # poisoned document or an injected record rather than from a transaction's guidance).
    if not reasons and any(_affirmed(RETRY_NOW, s) for s in sentences):
        reasons.append("retry_now_advice")
    return reasons


HEDGE = re.compile(r"\b(may|might|could|possibly|whether|if|unclear|unknown|not sure)\b\W+(\w+\W+){0,3}$", re.I)
UNKNOWN_STATUSES = {"unknown", "pending", "processing"}
# "refund" in TXN_FIELDS too: the record field the lifecycle check reads


def _affirmed(pattern: re.Pattern, text: str, hedges: bool = False) -> bool:
    """True if any match is not negated (or, with hedges=True, hedged) just before it."""
    for m in pattern.finditer(text):
        # Look back at most 40 characters, and never past the start of the sentence: a
        # "won't" in the previous sentence must not cancel a claim made in this one.
        before = re.split(r"[.!?;]\s", text[max(0, m.start() - 40):m.start()])[-1]
        if NEGATION.search(before) or (hedges and HEDGE.search(before)):
            continue
        return True
    return False


CREDENTIAL_REQUEST = re.compile(
    r"\b(send|email|share|reply with|text|give us|give me|post|dm)\b.{0,50}\b(full card number|card number|"
    r"card details|cvv2?|cvc|pin|password|one[- ]time (code|password)|otp|security code)\b", re.I | re.S)


REFUND_DONE = re.compile(r"\b(was|has been|got|were|been)\s+(fully\s+)?refunded\b|\brefund\s+(succeeded|completed|"
                         r"went through|was processed|has been processed)\b", re.I)
FULLY_REFUNDED = re.compile(r"\b(fully|completely|entirely)\s+refunded\b|\bfull refund\b", re.I)
RELATIVE_DATE = re.compile(r"\b(yesterday|today|tomorrow|tonight|last (night|week|month)|this (morning|afternoon|"
                           r"evening|week)|\d+\s+(days?|hours?|weeks?)\s+ago|earlier today)\b", re.I)


def lifecycle_check(answer: str, tool_log: list[dict]) -> list[str]:
    """Refund claims must match the refund record; dates must come from records, not 'yesterday'."""
    reasons = []
    for call in tool_log:
        res = call["result"]
        if call["name"] != "get_transaction" or res["status"] != "found":
            continue
        refund = res["data"].get("refund") or {}
        if refund and refund.get("status") != "succeeded" and _affirmed(REFUND_DONE, answer):
            reasons.append(f"refund_status_mismatch:record={refund.get('status')}")
        if refund and refund.get("amount") != res["data"].get("amount") and _affirmed(FULLY_REFUNDED, answer):
            reasons.append("refund_amount_mismatch:partial")
    if tool_log and _affirmed(RELATIVE_DATE, answer):
        reasons.append("unverifiable_relative_date")   # records carry timestamps; quote those
    return reasons


# ------------------------------------------------------------------ processor routing claims

def _default_processors() -> set[str]:
    from .processors.catalog import DEFAULT_PROFILES
    return {p.name for p in DEFAULT_PROFILES}


KNOWN_PROCESSORS = _default_processors()

# A claim that THIS payment moved between processors (subject first, so an incident summary
# such as "traffic failed over" or a policy reason such as "cascading will not help" is not one).
_PROCESSOR_ALT = r"(another|a second|a different|the next|a backup) (processor|acquirer)"
CASCADE_CLAIM = re.compile(r"\b(it|the (payment|transaction|charge)|this (payment|transaction)|txn_\w+)\b[^.]{0,60}?"
                           r"\b(cascaded|failed over|re-?routed|was (sent|retried|tried) (on|to|with|at) "
                           + _PROCESSOR_ALT + r")\b", re.I)
# Advice addressed to the reader to move the payment to another processor.
CASCADE_ADVICE = re.compile(r"(\b(you (should|can|could|may|might want to)|(we|i) (recommend|suggest)|go ahead and|"
                            r"please)\b|^\s*)\W*(send|route|resubmit|cascade|run|try|retry)\b[^.]{0,30}\b"
                            + _PROCESSOR_ALT + r"\b", re.I)
OUTAGE_CLAIM = re.compile(r"\b(is|was|were|went|has been|had been) (down|offline|unavailable|degraded|"
                          r"having an outage)\b|\b(outage|incident)\b", re.I)


def _routing_evidence(tool_log: list[dict]) -> tuple[str, set[str], set[str], dict[str, dict]]:
    """From this turn's tool results: the evidence text, txns with a recorded cascade, processors
    with a recorded processor-side fault, and processor health by name. Routing traces AND
    gateway logs count: logs carry route_selected, processor_attempt and cascade events."""
    from .processors.codes import PROCESSOR_FAULT_CODES
    found = [c for c in tool_log if c["result"]["status"] == "found"]
    blob = " ".join(json.dumps(c["result"]["data"]) for c in found).lower()
    cascaded: set[str] = set()
    errored: set[str] = set()
    health: dict[str, dict] = {}
    for c in found:
        data = c["result"]["data"]
        if c["name"] == "get_processor_health":
            health[data["processor"]] = data
        elif c["name"] == "get_routing_trace":
            for a in data.get("attempts") or []:
                if a.get("cascaded"):
                    cascaded.add(data["txn_id"])
                if a["code"] in PROCESSOR_FAULT_CODES:
                    errored.add(a["processor"])
        elif c["name"] == "search_transaction_logs":
            for e in data:
                if e.get("event") == "cascade":
                    cascaded.add(e["txn_id"])
                if e.get("event") == "processor_attempt" and e.get("code") in PROCESSOR_FAULT_CODES:
                    errored.add(e["processor"])
    return blob, cascaded, errored, health


def routing_check(answer: str, tool_log: list[dict], known_processors: set[str]) -> list[str]:
    """Processor names, cascades, cascade advice and outage claims must match the routing evidence."""
    from .tools import TXN_ID
    blob, cascaded, errored, health = _routing_evidence(tool_log)
    reasons = []
    low = answer.lower()
    named = {p for p in known_processors if re.search(rf"\b{re.escape(p)}\b", low)}
    reasons += [f"ungrounded_processor:{p}" for p in sorted(named)
                if not re.search(rf"\b{re.escape(p)}\b", blob)]

    looked_up = {c["input"].get("txn_id") for c in tool_log
                 if c["result"]["status"] == "found" and c["input"].get("txn_id")}
    sentences = _sentences(answer)
    for x in sentences:
        if not _affirmed(CASCADE_CLAIM, x):
            continue
        about = set(TXN_ID.findall(x)) or looked_up    # the txn the sentence names, else any looked up
        if not about:
            reasons.append("ungrounded_cascade_claim")
        elif not about & cascaded:
            reasons.append("cascade_claim_mismatch:record=no_cascade")
    advice = [c["result"]["data"].get("cascade_advice") or {} for c in tool_log
              if c["name"] == "get_routing_trace" and c["result"]["status"] == "found"]
    if any(a.get("another_processor_would_help") is False for a in advice) and \
            any(_affirmed(CASCADE_ADVICE, x) for x in sentences):
        reasons.append("cascade_advice_contradicts_policy")
    for x in sentences:
        if not _affirmed(OUTAGE_CLAIM, x):
            continue
        p = _nearest_processor(x, OUTAGE_CLAIM, named)
        if p and not (health.get(p, {}).get("incidents") or p in errored):
            reasons.append(f"ungrounded_outage_claim:{p}")
    return sorted(set(reasons), key=reasons.index)


def _sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?;])\s+", text) if x.strip()]


def _nearest_processor(sentence: str, pattern: re.Pattern, names: set[str]) -> str | None:
    """The processor an outage claim is about: the named processor closest to the claim."""
    m = pattern.search(sentence)
    if not m:
        return None
    low = sentence.lower()
    spots = [(abs(h.start() - m.start()), p) for p in names for h in re.finditer(rf"\b{re.escape(p)}\b", low)]
    return min(spots)[1] if spots else None


def credential_request_check(answer: str) -> list[str]:
    """The bot never asks for secrets. An answer that does was steered by injected text."""
    hits = [m.group(0) for m in CREDENTIAL_REQUEST.finditer(answer)]
    negated = [h for h in hits if re.search(r"\b(never|don't|do not|won't|will not)\b", answer[:answer.find(h)][-40:],
                                            re.I)]
    return ["credential_request"] if len(hits) > len(negated) else []


def evidence_check(answer: str, tool_log: list[dict], focus_txn: str | None,
                   known_processors: set[str] | None = None) -> GuardResult:
    """Deterministic hallucination checks against this turn's tool evidence.

    - Mentions of a txn id must be the focus txn or ones fetched this turn.
    - Amounts and decline codes stated must appear in tool data this turn
      (this also catches facts carried over from an earlier transaction).
    - Status polarity must match the record (no "approved" for a decline).
    - No status claim without a lookup this turn, or about a txn that wasn't found.
    - Every URL cited must come from a knowledge-base result this turn.
    Free-text explanations are left to the contextual grounding filter.
    """
    reasons: list[str] = []
    found_txns, missing_txns, evidence, statuses = set(), set(), [], {}
    for call in tool_log:
        res = call["result"]
        tid = call["input"].get("txn_id")
        if call["name"] == "get_transaction":
            if res["status"] == "found":
                found_txns.add(tid)
            elif res["status"] == "not_found":  # an outage ("error") is not a missing transaction
                missing_txns.add(tid)
            if res["status"] == "found":
                statuses[tid] = str(res["data"].get("status", "")).lower()
        if res["status"] == "found":
            evidence.append(json.dumps(res["data"]))
    blob = " ".join(evidence)

    # Only a knowledge-base page's own source_url may be cited. A URL that merely appears
    # inside a record (merchant name, log message, doc body) is attacker-writable text.
    kb_urls = {h.get("source_url", "") for c in tool_log
               if c["name"] == "search_knowledge_base" and c["result"]["status"] == "found"
               for h in (c["result"]["data"] or []) if isinstance(h, dict)}
    for url in set(URL.findall(answer)):
        if url.rstrip(".,;:") not in kb_urls:
            reasons.append(f"unverified_url:{url}")
    reasons += credential_request_check(answer)
    reasons += lifecycle_check(answer, tool_log)
    reasons += routing_check(answer, tool_log, known_processors or KNOWN_PROCESSORS)
    pos, neg = bool(POSITIVE.search(answer)), bool(NEGATIVE.search(answer))
    if len(statuses) == 1:
        actual = next(iter(statuses.values()))
        # An affirmed (un-negated) claim of the opposite outcome is a contradiction,
        # even if the answer also states the right one ("failed ... was approved").
        if actual in NEGATIVE_STATUSES and _affirmed(POSITIVE, answer):
            reasons.append(f"status_mismatch:record={actual}")
        if actual == "approved" and _affirmed(NEGATIVE, answer):
            reasons.append(f"status_mismatch:record={actual}")
        # Outcome unknown (e.g. processor timeout): any definite claim either way is
        # made up, and risks a double charge or a lost sale. Hedged wording is fine.
        if actual in UNKNOWN_STATUSES and (_affirmed(POSITIVE, answer, hedges=True) or
                                           _affirmed(NEGATIVE, answer, hedges=True)):
            reasons.append(f"definite_outcome_claimed:record={actual}")
    if not found_txns and not missing_txns and STATUS_CLAIM.search(answer):
        reasons.append("status_claim_without_lookup")
    reasons += retry_advice_check(answer, tool_log)
    low_blob = blob.lower()
    for claim, needs in RECORD_CLAIMS:
        if claim.search(answer) and needs not in low_blob:
            reasons.append(f"ungrounded_{needs}_claim")

    from .tools import TXN_ID
    for tid in set(TXN_ID.findall(answer)):
        if tid not in found_txns and tid not in missing_txns and tid != focus_txn:
            reasons.append(f"unverified_txn_reference:{tid}")
    for amt in set(AMOUNT.findall(answer)):
        if amt not in blob:
            reasons.append(f"ungrounded_amount:{amt}")
    for code in set(CODE.findall(answer)):
        if code not in blob and not code.isalpha():
            reasons.append(f"ungrounded_code:{code}")
    if missing_txns and not found_txns:
        low = answer.lower()
        says_missing = any(p in low for p in ("not found", "couldn't find", "could not find",
                                               "no record", "doesn't exist", "does not exist", "no transaction"))
        if not says_missing or pos or neg:
            reasons.append("claims_about_missing_transaction")
    if reasons:
        return GuardResult("block", BLOCKED_OUTPUT_MSG, reasons)
    return GuardResult("allow", answer)


def make_guardrail(settings):
    """Factory (delegates to the guardrail registry in finbot/registry.py)."""
    from .registry import GUARDRAILS
    return GUARDRAILS.create(settings.guardrail_provider, settings=settings)
