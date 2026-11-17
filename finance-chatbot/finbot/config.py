"""Runtime settings. Everything comes from env vars; no secrets in code.

AWS credentials are resolved by boto3's default chain (IAM role / SSO / STS),
never static access keys in config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # LLM: "bedrock" (Claude on Amazon Bedrock), "anthropic" (Claude API, or a
    # gateway via ANTHROPIC_BASE_URL), or "fake" (offline tests/demo).
    llm_provider: str = field(default_factory=lambda: _env("FINBOT_LLM_PROVIDER", "fake"))
    model: str = field(default_factory=lambda: _env("FINBOT_MODEL", ""))
    effort: str = field(default_factory=lambda: _env("FINBOT_EFFORT", "medium"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1"))

    # Guardrails: "bedrock" (ApplyGuardrail API) or "local" (regex fallback).
    guardrail_provider: str = field(default_factory=lambda: _env("FINBOT_GUARDRAIL", "local"))
    guardrail_id: str = field(default_factory=lambda: _env("FINBOT_GUARDRAIL_ID"))
    guardrail_version: str = field(default_factory=lambda: _env("FINBOT_GUARDRAIL_VERSION", "DRAFT"))

    # Knowledge base
    kb_path: str = field(default_factory=lambda: _env("FINBOT_KB_PATH", "kb_index.json"))
    data_dir: str = field(default_factory=lambda: _env("FINBOT_DATA_DIR", "data"))  # synthetic dataset
    embeddings: str = field(default_factory=lambda: _env("FINBOT_EMBEDDINGS", "none"))  # none | titan
    embed_model: str = field(default_factory=lambda: _env("FINBOT_EMBED_MODEL", "amazon.titan-embed-text-v2:0"))

    # Session store: "sqlite" (single host / dev) or "dynamodb" (production, multi-worker)
    store_provider: str = field(default_factory=lambda: _env("FINBOT_STORE_PROVIDER", "sqlite"))
    store_path: str = field(default_factory=lambda: _env("FINBOT_STORE", "sessions.db"))
    dynamo_table: str = field(default_factory=lambda: _env("FINBOT_DYNAMO_TABLE", "finbot-sessions"))
    lease_s: float = 60  # worker ownership of a session; renewed by heartbeat before each model call

    # Security: tenant, groups and role come from a verified token, never from the request body.
    # "hmac" = HS256 shared secret (dev/tests); "jwks" = OIDC provider (Cognito/Okta/Entra) public keys.
    auth_mode: str = field(default_factory=lambda: _env("FINBOT_AUTH", "hmac"))
    auth_secret: str = field(default_factory=lambda: _env("FINBOT_AUTH_SECRET"), repr=False)
    auth_issuer: str = field(default_factory=lambda: _env("FINBOT_AUTH_ISSUER"))
    auth_audience: str = field(default_factory=lambda: _env("FINBOT_AUTH_AUDIENCE", "finbot"))
    auth_jwks_url: str = field(default_factory=lambda: _env("FINBOT_AUTH_JWKS_URL"))
    # HS256 key rotation: the current key's id, and previous keys still accepted ("kid:secret,...").
    auth_kid: str = field(default_factory=lambda: _env("FINBOT_AUTH_KID", "k1"))
    auth_previous_secrets: str = field(default_factory=lambda: _env("FINBOT_AUTH_PREVIOUS_SECRETS"), repr=False)

    # Encryption at rest (finbot/crypto.py): session state, user text and answers in the store.
    # "none", "local" (key ring in FINBOT_LOCAL_KEYS; dev/tests) or "kms" (AWS KMS key FINBOT_KMS_KEY_ID).
    encryption: str = field(default_factory=lambda: _env("FINBOT_ENCRYPTION", "none"))
    kms_key_id: str = field(default_factory=lambda: _env("FINBOT_KMS_KEY_ID"))
    local_keys: str = field(default_factory=lambda: _env("FINBOT_LOCAL_KEYS"), repr=False)
    # Refuse to read values written before encryption was turned on.
    encryption_required: bool = field(default_factory=lambda: _env("FINBOT_ENCRYPTION_REQUIRED", "0") == "1")

    # Rate limits per verified user and per tenant (finbot/ratelimit.py); 0 turns them off.
    rate_user_per_min: float = field(default_factory=lambda: float(_env("FINBOT_RATE_USER_PER_MIN", "30")))
    rate_tenant_per_min: float = field(default_factory=lambda: float(_env("FINBOT_RATE_TENANT_PER_MIN", "600")))
    # pod: in-process buckets; shared: counters in the session store (exact across pods);
    # auto: shared with the DynamoDB store, pod otherwise.
    # IAM role confined to one tenant's session items; set it to enforce isolation below the app.
    # Tool-call rules for the security gateway: JSON list of {id, tool, roles, effect, reason}.
    tool_policy_path: str = field(default_factory=lambda: _env("FINBOT_TOOL_POLICY"))
    tenant_role_arn: str = field(default_factory=lambda: _env("FINBOT_TENANT_ROLE_ARN"))
    rate_scope: str = field(default_factory=lambda: _env("FINBOT_RATE_SCOPE", "auto"))

    # Audit log hash chain: HMAC key for the chain (finbot/auditlog.py). Empty: plain SHA-256.
    audit_key: str = field(default_factory=lambda: _env("FINBOT_AUDIT_KEY"), repr=False)
    # Delegation grants for tool calls (finbot/agent_identity.py). Empty: a random per-process key.
    grant_key: str = field(default_factory=lambda: _env("FINBOT_GRANT_KEY"), repr=False)

    # Control plane: how often each worker re-reads shared model config (drain/restore/routing)
    control_sync_s: float = 2.0

    # Queue worker (SQS in production)
    queue_url: str = field(default_factory=lambda: _env("FINBOT_QUEUE_URL"))
    max_receives: int = 5  # then dead-letter: a message that keeps failing needs a human

    # Knowledge read path: "index" (local BM25+dense JSON), "vector" (VectorKnowledge over
    # FINBOT_VECTOR_STORE + FINBOT_VECTOR_EMBEDDER, written by scripts/ingest.py) or
    # "bedrock_kb" (managed Bedrock Knowledge Base FINBOT_BEDROCK_KB_ID)
    kb_provider: str = field(default_factory=lambda: _env("FINBOT_KB_PROVIDER", "index"))
    vector_store: str = field(default_factory=lambda: _env("FINBOT_VECTOR_STORE", "local:kb_vectors.json"))
    vector_embedder: str = field(default_factory=lambda: _env("FINBOT_VECTOR_EMBEDDER",
                                                              "bedrock:amazon.titan-embed-text-v2:0@1024"))
    bedrock_kb_id: str = field(default_factory=lambda: _env("FINBOT_BEDROCK_KB_ID"))

    # Domain plug-ins this deployment offers (finbot/domains). Leave one out to switch it off,
    # e.g. one with no card payments leaves "payments" out.
    domains: str = field(default_factory=lambda: _env(
        "FINBOT_DOMAINS", "accounts,transfers,kyc,lending,issuing,onboarding,disputes,mortgages,bnpl,insurance,"
                          "subscriptions,payments"))

    # Session limits
    max_tool_rounds: int = 6
    # Regenerate once with verifier feedback before falling back. 0 = never call the model a
    # second time: a failed draft is replaced by the record-built answer (or the refusal).
    repair_attempts: int = field(default_factory=lambda: int(_env("FINBOT_REPAIR_ATTEMPTS", "1")))
    # When the model's answer can't be verified, state what the record shows instead of refusing.
    record_fallback: bool = field(default_factory=lambda: _env("FINBOT_RECORD_FALLBACK", "1") != "0")
    # A cited help page older than this many days gets a "may be out of date" note. 0 = off.
    kb_max_age_days: int = field(default_factory=lambda: int(_env("FINBOT_KB_MAX_AGE_DAYS", "540")))
    llm_retry_attempts: int = 3  # per model target: transient errors (429/5xx/timeouts), full-jitter backoff
    # Model orchestrator (finbot/models.py)
    model_chain: str = field(default_factory=lambda: _env("FINBOT_MODEL_CHAIN"))  # ordered failover chain
    routing: str = field(default_factory=lambda: _env("FINBOT_ROUTING", "single"))  # single | tiered
    # Adaptive guardrails (finbot/adaptive): "static" = baseline policy, "store" = controller-managed
    guard_policy_source: str = field(default_factory=lambda: _env("FINBOT_GUARD_POLICY", "static"))
    guard_labels_path: str = field(default_factory=lambda: _env("FINBOT_GUARD_LABELS", "guard_labels.jsonl"))
    # Write requests become tickets for people (finbot/tickets.py). Empty = in memory (dev/tests).
    tickets_path: str = field(default_factory=lambda: _env("FINBOT_TICKETS"))
    fast_model: str = field(default_factory=lambda: _env("FINBOT_FAST_MODEL"))  # chain for simple turns
    model_breaker_threshold: int = 3   # consecutive failed calls before a model's breaker opens
    model_breaker_cooldown_s: float = 30  # first half-open probe after this; doubles on failed probes
    turn_deadline_s: float = 90  # max wait per turn across retries/failover; then "try again"
    escalate_after_fallbacks: int = 2  # offer a human after this many unverifiable answers in a row
    support_contact: str = field(default_factory=lambda: _env("FINBOT_SUPPORT_CONTACT", "the Support Center"))
    max_turns_per_thread: int = 30  # then roll to a fresh thread seeded with typed state
    # ...or earlier, when the history passes this size (characters, ~4 per token). Bounds the
    # model's context and the stored session (DynamoDB items are capped at 400 KB).
    max_history_chars: int = 120_000
    # Cache (finbot/cache.py): help-page searches and model breaker health only, never records.
    cache: str = field(default_factory=lambda: _env("FINBOT_CACHE", "memory"))   # memory | redis | none
    cache_url: str = field(default_factory=lambda: _env("FINBOT_CACHE_URL"))     # rediss://... for redis
    kb_cache_ttl_s: float = field(default_factory=lambda: float(_env("FINBOT_KB_CACHE_TTL_S", "300")))
    health_cache_s: float = 1.0   # breaker health read from the store at most this often per worker
    # AI observability (finbot/telemetry.py): none | log | otel. No prompt, answer or record data in spans.
    telemetry: str = field(default_factory=lambda: _env("FINBOT_TELEMETRY", "none"))
    telemetry_path: str = field(default_factory=lambda: _env("FINBOT_TELEMETRY_PATH"))
    recap_requests: int = 8   # user requests carried into a fresh thread (each cut to 200 characters)

    def resolved_model(self) -> str:
        if self.model:
            return self.model
        if self.llm_provider == "bedrock":
            return "anthropic.claude-opus-5-5"
        return "claude-opus-5-5"
