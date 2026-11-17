# finbot technical design

*November 2026. Status: reference implementation, tested offline, not deployed.*
How this generalises to a multi-agent orchestration platform and a multi-tenant AI platform is in
[platform-design.md](platform-design.md).
The architectural overview and the patterns used are in [design.md](design.md). Security
details are in [security.md](security.md), recovery details in [reliability.md](reliability.md),
and domains in [domains.md](domains.md). This document specifies components, interfaces, data,
sequences, resiliency and deployment at the level needed to build, operate or review the system.

## 1. Scope

**Goal.** Answer customer and support-agent questions about financial records (accounts,
transfers, KYC cases, loans, cards, applications, disputes, mortgages, BNPL plans, insurance
claims, subscriptions, card payments) without stating anything the records do not support.

**Requirements.**

| ID | Requirement | Where it is met |
|---|---|---|
| F1 | Every amount, currency, date, status, cause, reason code, record id, link and contact detail in an answer is backed by this turn's tool data | `verification.py`, `guardrails.py`, `domains/checks.py`, `provenance.py` |
| F2 | Requests to change anything become tickets for a person | `actions.py`, `tickets.py` |
| F3 | Help-page answers cite the page or say there is no documentation | `guardrails.citation_check` |
| F4 | A domain can be added or switched off without changing the core | `domains/base.py`, `FINBOT_DOMAINS` |
| S1 | Tenant comes only from a verified token; another tenant's data is indistinguishable from missing data | `security.py`, `service.py`, `tools.py` |
| S2 | Card data is masked before it is stored, logged, queued, shown or sent to a model | `pii.py`, `dlp.py` |
| S3 | An injection that reaches the model cannot fetch other records, state unsupported facts, cite planted links or ask for secrets | provenance, gate, `credential_request_check` |
| R1 | A worker crash loses at most the uncommitted turn; no turn is committed twice | `runtime.py`, `store*.py` |
| R2 | A model outage fails over; a guardrail outage fails closed | `models.py`, `orchestrator.py` |
| R3 | A user waits at most the turn deadline (90 s) | `resilience.retry_call`, `models.py` |

**Non-goals.** Write actions on records; answers in languages other than English (sent to a
person); advice, rules interpretation or discretionary decisions; model training.

## 2. Components

| Module | Responsibility | Depends on |
|---|---|---|
| `http_api.py` | WSGI adapter: routes, JSON parsing, size and type limits, status codes | `service.py` |
| `service.py` (`ChatService`) | Trust boundary: verify token, rate limit, authorize, validate, DLP scan in and out | `security.py`, `ratelimit.py`, `dlp.py`, `runtime.py` |
| `queue.py`, `worker_main.py` | SQS FIFO consumer: one message at a time, retry later, dead-letter | `runtime.py` |
| `runtime.py` (`Worker`) | Lease, load, journal, run, heartbeat, compare-and-swap commit, recovery | `store*.py`, `orchestrator.py` |
| `orchestrator.py` (`Chatbot`) | One turn: the stage pipeline, the model tool loop, the gate, the output guard, audit | everything below |
| `verification.py` | `VerifierChain` and `HallucinationGate` (repair and fallback policy) | verifiers |
| `guardrails.py` | Evidence checks; Bedrock or local guardrail adapters | `provenance.py` |
| `provenance.py` | Trusted view of tool data; contact check | `dlp.py` patterns |
| `domains/` | Plug-in contract, registry, twelve domains, shared status and cause checks | — |
| `recordanswer.py` | Answer built by code from one record when the model's answer fails | `domains/` |
| `models.py` | Model engine: failover chain, breakers, refusal fallback, tiered routing | `llm.py`, `resilience.py` |
| `tools.py` | Tool schemas and runners, tenant scoping, field allow-list, backend retries and breakers | `domains/`, `agent_identity.py` |
| `untrusted.py` | Injection screening, field minimisation, spotlighting, prompt-leak canary | `pii.py` |
| `memory.py` | Typed session state: entities, facts with provenance, focus, user-typed ids, tickets | — |
| `agent_identity.py` | Per-turn delegation grant, verified at every tool call | — |
| `crypto.py` | Envelope encryption (AES-256-GCM, KMS-wrapped data keys), bound to tenant, session and purpose | KMS |
| `security_gateway.py` | Decides on every tool call (schema, rules, bulk access, human approval), watches session behaviour, writes security events | `tickets.py`, `auditlog.py` |
| `tenancy.py` | Request tenant scope; session items through per-tenant credentials | STS, IAM |
| `store.py`, `store_dynamo.py` | Session store: SQLite or DynamoDB behind one contract | — |
| `adaptive/` | Learned guard rules: proposals, gate, shadow, canary, rollback; session risk score | eval and red-team suites |
| `control.py` | Control plane: drain or restore a model, routing mode | store |
| `knowledge.py`, `ingestion/` | Help-page index; ingestion pipeline (screen, chunk, embed, upsert) | vector store adapters |
| `reconcile.py` | Reconciliation jobs: tickets vs systems of record, audit log vs committed turns, help-page index vs source | `tickets.py`, `auditlog.py` |
| `cache.py` | Cache port (memory, Redis protocol, none) and the only two cached reads: help-page search and breaker health | — |
| `telemetry.py` | AI observability: OpenTelemetry GenAI spans for turn, model call, tool call, verification | OpenTelemetry API (optional) |
| `container.py`, `registry.py` | Composition root and provider factories | — |

Dependency rule: the orchestration layer imports no vendor SDK. Vendors sit behind
`ports.py` and are chosen by configuration.

## 3. Turn lifecycle

```
client      ChatService           Worker               Chatbot                         Store
  │ POST /v1/sessions/{id}/messages {msg_id, text}
  │──────────►│ verify token, rate limit, own session?, validate, DLP in
  │           │────────────────────►│ claim lease ───────────────────────────────────────►│
  │           │                     │ load committed state; msg_id seen? → stored reply ◄─│
  │           │                     │ journal turn (masked text) ─────────────────────────►│
  │           │                     │──────────────────────►│ stages 1–6 (may end the turn)
  │           │                     │                       │ model loop ⇄ tools (grant, provenance)
  │           │                     │                       │ gate: verify → repair → record answer
  │           │                     │                       │ output guard, PII mask, audit
  │           │                     │◄──────────────────────│ Turn
  │           │                     │ renew lease; CAS commit (version + lease) ───────────►│
  │           │◄────────────────────│ reply + meta
  │◄──────────│ DLP out
```

**Stages** (`Chatbot.stages`, in order; each returns a reply to end the turn, or passes):

1. Risk lock: a session over the risk threshold goes to a person and stays locked.
2. Input guard: screening and the configured guardrail; fails closed if unreachable.
3. Language: non-English goes to a person with a fixed reply in that language.
4. Learned policy: the active guard policy's input rules (block or escalate).
5. Focus: which record the turn is about; records ids the user typed (argument provenance).
6. Write request: a change request becomes a ticket, after a verified lookup of the record.
7. Model answer: tool loop (at most 6 rounds), hallucination gate, output guard, stale-source note.

## 4. Interfaces

### 4.1 HTTP API (`http_api.py`)

All endpoints except `/healthz` are `POST`, take a bearer token, accept JSON only and reject
query strings.

| Endpoint | Body | Response | Errors |
|---|---|---|---|
| `GET /healthz` | — | `200 {"ok": true}` | — |
| `POST /v1/sessions` | — | `201 {"session_id"}` | 401 bad token, 403 role, 429 rate limit |
| `POST /v1/sessions/{id}/messages` | `{"msg_id", "text"}`; `msg_id` 8–64 chars `[A-Za-z0-9_-]`, client-generated and reused on resend; `text` 1–4,000 chars | `200` reply (below) | 400 invalid, 401, 403 (unknown and other-tenant sessions look the same), 429 |
| `POST /v1/sessions/{id}/cancel` | `{"msg_id"}` | `202 {"cancelled"}`; the turn checks cancellation before its next model call and before commit | 400, 401, 403 (owner only), 429 |
| `POST /v1/admin/revocations` | `{"jti", "expires_at"}` or `{"subject"}` | `200` | 401, 403 (admin only) |

A session binds the subject, tenant, role, groups and scopes at creation. Each send compares the
current verified token's role, groups and scopes with that snapshot **before** running the
worker or returning a deduplicated reply. Changed permissions require a new session (403),
so prior history, record facts and cached answers cannot survive a permission reduction.
Renewed tokens with the same permissions still work; ordering of groups/scopes does not matter.
The owner may still cancel pending work after a scope/group change.

Reply body:

```json
{"answer": "...", "blocked_by": null, "reasons": [], "focus": "xfer_...",
 "repaired": false, "escalated": false, "tier": null, "models": ["bedrock:...@us-east-1"],
 "tools": ["get_transfer"], "version": 7, "deduplicated": false, "dlp": ["pan"]}
```

`blocked_by` is one of `null` (answered), `input_guardrail`, `output_guardrail`,
`evidence_guard`, `escalation`, `language`, `write_request`, `session_risk`, `unavailable`, `cancelled`. A tool call held for approval comes back to the model as a `refused` envelope with a ticket id. On
`unavailable` the reply carries `retry_after_s` and the turn is not committed, so resending the
same `msg_id` later gets a real answer. `dlp` lists categories found in the request, never values.

### 4.2 Tool contract

Every tool returns one envelope:

```json
{"status": "found | not_found | error | refused", "source": "billing_db",
 "data": {...} | [...] | null, "note": "optional instruction-free note", "screened": ["path:reason"]}
```

- `not_found` covers both a missing record and another tenant's record.
- `error` is an outage; the model is told to say the system is unavailable.
- `refused` comes from code, not the backend: argument provenance or a missing grant scope.
- `data` has passed the field allow-list, screening and PII masking before the model sees it.
- The envelope is wrapped in a spotlight delimiter with a per-turn nonce.

Tools: `get_transaction`, `search_transaction_logs`, `get_routing_trace`,
`get_processor_health`, `search_knowledge_base`, and one `get_<record>` per enabled domain.

### 4.3 Domain plug-in contract (`domains/base.py`)

A `Domain` is a frozen dataclass registered at import time:

| Field | Type | Meaning |
|---|---|---|
| `name`, `noun`, `prefix`, `tool`, `id_arg`, `source`, `description` | str | naming, tool schema, id pattern `^<prefix>_[A-Za-z0-9]{6,}$` |
| `fields` | frozenset | allow-list; any other field never leaves the tool |
| `statuses` | dict | answer word → record value |
| `open_statuses` | frozenset | undecided states that may not be called decided |
| `policy` | dict[str, `Rule`] | reason key → meaning, customer step, support step, `resend`, `resend_rule`, `only_if` |
| `reason_key`, `summary` | callables | record → policy key; record → one validated sentence or `None` |
| `causes`, `cause_phrases` | dicts | reason key → cause; cause → regex |
| `implied`, `status_causes` | dicts | statuses that imply earlier ones; statuses that carry a cause |

### 4.4 Ports (`ports.py`)

`LLMClient`, `Guardrail`, `TransactionStore`, `LogSearch`, `KnowledgeSearch`, `HealthStore`,
`SessionRepository`, `MessageSource` and `QueueMessage` are protocols in `ports.py`; ticket
stores, vector stores, embedders, token verifiers and policy sources follow the same pattern in
their own modules. Each has an in-memory or local adapter for tests and an AWS adapter
(Bedrock, Bedrock Guardrails, DynamoDB, SQS, OpenSearch or pgvector, Cognito/Okta/Entra via JWKS).

## 5. Data model

**Session state** (`memory.SessionMemory`, sealed with `crypto.py` before storage):

| Field | Content |
|---|---|
| `entities` | per record id: facts (`key`, `value`, `source`, `at`, `superseded_by`) taken only from tool results, and `not_found` |
| `focus` | the record the conversation is about |
| `user_ids` | ids the user typed: the only ids a tool may be called with |
| `tickets` | tickets code opened this session |
| `risk` | session risk score |
| `consecutive_fallbacks` | unverifiable answers in a row (2 → handoff) |
| `turns_in_thread`, `recap` | thread rollover after 30 turns or a size limit, with the user's own earlier requests carried over |

Model prose is never stored as a fact.

**Ticket** (`tickets.Ticket`): id, tenant, session, requesting role, kind, record id, masked
request text, the verified record facts at request time, policy note, status, assignee,
outcome note, timestamps.

**Audit record** (one JSON line per turn): session, tenant, masked user text, `blocked_by`,
reasons, focus, latency, repaired, escalated, tier, models used, guard policy version, risk,
shadow-policy decision, and per tool call the name, masked input, status and what screening
removed. DLP records hold categories and counts only. The audit trail has its own key and a
write-once bucket in production.

**DynamoDB keys** (`store_dynamo.py`): session META item (owner, lease expiry, version, state);
turn items `T#<msg_id>` (journal and committed reply); `H#<model>` breaker health;
`C#<key>` control configuration; TTL on `expires`. Tickets use the separate shared partition
`TICKETS` with sort key `K#<ticket_id>`.

**Session and journal lifetime.** Both stores enforce a 30-minute idle timeout and a fixed
24-hour maximum lifetime (`hard_expires`, set at creation). Each commit extends idle expiry
only up to that fixed deadline. Every DynamoDB turn, including an incomplete turn, retains its
journal item until `hard_expires`; later turns cannot outlive earlier deduplication records.
This avoids updating an unbounded number of old items on every commit. Idle session META
items may disappear sooner; orphaned turn items expire at the fixed deadline. Reads reject
expired sessions even before DynamoDB's asynchronous TTL deletion runs.

**Upgrade behavior.** Old DynamoDB sessions without `hard_expires` are refused as expired:
their earlier turn items might already have disappeared, so safe deduplication cannot be
reconstructed. Clients must start new sessions. SQLite adds the column in a serialized
migration, retaining state and journals and bounding old sessions to 24 hours from their last
update. Do not run old and new store writers concurrently during this upgrade.

## 6. Verification

```
draft ─► VerifierChain ─ allow ─────────────────────────────────────────────► output guard
            │ block (reasons)
            ▼
         repair once on the strong tier, with the reasons ─► chain on the repair's own tool results
            │ still blocked                                           │ allow → output guard
            ▼
         record_answer(tool log) ─► chain ─ allow ─► output guard (marked records_only)
            │ None or blocked
            ▼
         "I won't guess" (+ handoff after 2 in a row)
```

| Verifier | Checks |
|---|---|
| `EvidenceVerifier` | amounts, currencies, percentages, dates, times, card digits and brand, codes, record ids, URLs (help-page `source_url` only), contacts, payment status polarity, status without lookup, retry and routing advice, citations |
| `ReasonVerifier` | a plain-words cause matches the payment record's failure key |
| `DomainVerifier` | status (tense- and subject-aware), cause, reason keys and nested events for every domain record |
| `LanguageVerifier` | answer is English |
| `PolicyVerifier` | learned commitment rules ("guaranteed", "you'll get a refund") |
| `TicketVerifier` | ticket ids and "I've raised a ticket" only when code opened one |

**Value provenance** (`provenance.py`). The detail and amount checks read a trusted view of the
tool data in which strings under third-party keys (`merchant_name`, `merchant`, `descriptor`,
`message`, `description`, `memo`, `payee_name`, `counterparty`) are replaced, at any depth.
Phone numbers and emails in an answer must appear in that trusted view. Help-page text counts
as trusted.

**Record-built answer** (`recordanswer.py`). Built only for a single record lookup in this turn:
the domain's `summary` (every repeated field validated) plus the policy guidance for the
reader's role. Returns `None` for help-page questions, several records, or fields that fail
validation.

## 7. Resiliency

### 7.1 Failure modes

| Failure | Detection | Behaviour | User sees | Test |
|---|---|---|---|---|
| Model 429, 5xx, timeout | exception class | retry with jitter inside the deadline, then the next target in the chain; mid-turn failover continues the same history | normal answer, slower | `test_model_engine`, `test_boundaries` |
| Model keeps failing | 3 consecutive failures | breaker opens; target skipped; one probe after 30 s, cooldown doubles to 5 min on failure; state shared by all workers through the store | normal answer from the backup | `test_breaker_state_is_shared_across_workers` |
| Model refuses | `stop_reason == refusal` | next target; its answer is verified like any other | verified answer or refusal | `test_refusal_fallback_cannot_bypass_guards` |
| 400 or auth error from a model | exception class | raised, not failed over (a configuration bug) | an error for the operator to fix | `test_model_engine` |
| Guardrail service down | exception | fail closed; no model call | "unavailable", retry later | `test_guardrail_outage_fails_closed_...` |
| Backend tool slow or down | exception | 3 attempts with backoff per backend (`txn_db`, `log_search`, `knowledge_base`, `records`), breaker per backend; then an `error` envelope | "temporarily unavailable"; no invented answer | `test_retry_routing`, `test_processors` |
| Dependency outage during a turn | `ServiceUnavailable` | history and memory rolled back to the turn's start; turn not committed | "unavailable" with a reference and `retry_after_s`; resending the same `msg_id` gets a real answer later | `test_boundaries`, `test_layers` |
| Worker crash mid-turn | lease expiry | nothing committed; the next worker re-runs from the last commit (tools are read-only) | the answer, after up to one lease | `test_1_crash_mid_turn_...` |
| Crash after commit, before reply | client resend | stored reply returned, no model call | the same answer | `test_2_crash_after_commit_...` |
| Stalled worker returns late | version or lease mismatch | its commit fails with `StaleWrite`; heartbeat stops it early | the new worker's answer | `test_3_late_worker_...`, `test_heartbeat_stops_...` |
| Long but healthy turn | — | lease renewed before every model call and before commit | normal answer | `test_long_healthy_turn_keeps_its_lease` |
| Follow-up lands on another pod | — | the lease is released when a turn ends normally (left to expire after an exception, as after a crash) | no wait | `test_lease_is_released_after_a_turn` |
| Same session, two threads in one pod | in-flight set held through lease release | the second gets `LeaseHeld` (409, retry); DynamoDB release removes only lease attributes, conditioned on owner, version and lease expiry | one answer; no rollback of a newer commit | `test_deployment.ReleaseExclusion`, `test_store_contract.DynamoRelease` |
| Everything slow | turn deadline | retries and failover stop at 90 s | "try again", turn uncommitted | `test_retries_and_failover_stop_at_the_turn_deadline` |
| Duplicate delivery | `msg_id` in the journal | deduplicated | the same answer | `test_layers.QueueWorker` |
| Poison message | receive count | retried with backoff, dead-lettered after 5 receives | — | `test_layers.QueueWorker` |
| Forged tenant in a queue message | checked against the session | dead-lettered | — | `test_layers.QueueWorker` |
| Unknown outcome of an external action | `begin_action` intent without a result | marked `unknown`; not re-sent; reconciled with the system of record first | — | `test_4_unknown_external_action_...` |
| Guard policy store unreadable | exception | keep the last good policy, else the baseline; guards never switch off | normal | design rule (adaptive/source.py); only the empty-store case is tested |
| User cancels a turn | cancel flag, checked before start, every model call and after the final output checks | stop before the next model call or commit; commit the pre-turn state with a fixed answer; a resend returns "cancelled" | "Stopped. I didn't finish that request…" | `test_cancellation` |
| Pod shutdown (rollout, scale-in) | SIGTERM | stop receiving, finish the turn in hand, exit; grace period above the deadline | normal | `worker_main` design |

Cancellation remains cooperative: a call already in flight finishes, then its result is
discarded if the flag is present at the final checkpoint. Cancellation arriving after that
checkpoint may lose the race to commit; it does not undo an already committed turn. Session
history and facts roll back, while raised risk is retained. Tickets already created in the
separate ticket store remain for human review; cancellation does not undo external work.

### 7.2 Time budgets

| Budget | Value | Rule |
|---|---|---|
| Turn deadline | 90 s | every retry delay and failover stays inside it |
| Session lease | 60 s | renewed before each model call and before commit |
| Session activity | 30 min idle, 24 h absolute | activity never extends the fixed journal retention horizon |
| Queue visibility timeout | 150 s | must exceed the deadline; the worker refuses to start otherwise |
| Breaker open → probe | 30 s, doubling to 300 s | one probe per worker |
| Control-plane sync | 2 s | drain or routing change seen by every worker |
| Token revocation | ≤ 2 s | through the session store |

### 7.3 Gaps and planned additions

1. ~~The API runs turns in-process.~~ Done: `FINBOT_DISPATCH=queue` (`dispatch.py`). The API
   returns a committed answer if there is one, else enqueues and polls the store for the commit
   up to `FINBOT_DISPATCH_WAIT_S`, then answers `202` with `Retry-After`; the client resends the
   same `msg_id`. An API pod that dies after enqueuing loses nothing. `tests/test_dispatch.py`.
2. **No load shedding or per-tenant bulkhead inside the worker pool.** Rate limits cap each user
   and tenant, but one tenant's slow records backend can still occupy workers until its breaker
   opens. A per-backend concurrency cap would bound that.
3. ~~Breaker health is read from the store on each check.~~ Done: `CachedHealth` reads it at most
   once a second per worker; a worker's own publish is seen at once (section 7.4).
4. **No timer-based heartbeat** for a single model call that can outlast the lease (long
   streaming output); today the heartbeat runs before each call.
5. **Never exercised on real infrastructure.** Failover between Bedrock regions, DynamoDB
   conditional writes under contention, SQS redelivery and pod kills have only been tested
   against in-memory fakes that evaluate the same conditions.

### 7.4 Consistency and reconciliation

| Data | Consistency | Reconciliation |
|---|---|---|
| Session state and turn journal | strong: consistent reads; commit is a compare-and-swap on version and lease; one commit per `msg_id` | — (verified by `scripts/durability_test.py`) |
| Records the bot reads | read fresh every turn, never cached | — |
| Tickets resolved by the desk | the desk records what a person did | `reconcile_tickets`: a "done" ticket must show on the record (refund issued, cancelled, card active); else `mismatch`. Kinds no record can show (retry, limit change) are `not_verifiable` |
| Turn audit log | written before the commit, so a crash between them leaves a line for a turn that never counted | `reconcile_audit`: every committed turn has a line (`committed_without_audit` is a gap); uncommitted turns are named (`uncommitted`, `uncommitted_outage`, `uncommitted_cancelled`); re-runs counted (`rerun`). Lines carry the client's `msg_id` |
| Help-page index | nightly sync adds and updates only | weekly full sync deletes pages gone from the source; `reconcile_index` reports `missing`, `stale`, `orphaned` and `quarantined_but_indexed` |
| External actions (future write tools) | intent recorded before the call (`begin_action`), keyed by `op_id` | a pending action left by a crashed worker becomes `unknown` on takeover, and `Worker.action_checker` settles it with the system of record before the next turn |
| Rate-limit counters | approximate by design: sliding window, about one extra request at a boundary | — |
| Money (ledger vs processor settlement) | not finbot's: the systems of record reconcile it; the bot only reads | — |

`scripts/reconcile.py tickets|audit|index` runs the jobs, writes problems to the audit stream
`reconciliation` and exits 1 on any problem; `deploy/k8s/ingest-cronjob.yaml` schedules the
ticket and index jobs daily and the full sync weekly. One limit found while testing: ingestion
skips a page whose version did not change, so content edited in place without a version bump is
not re-screened; `reconcile_index` still flags such a page if it now fails screening.

Audit reconciliation is explicitly scoped by the operator:

```bash
python scripts/reconcile.py audit --audit audit.jsonl --tenant m_100
```

The job enters `tenant_scope(m_100)` for session reads, filters a shared log to that tenant,
and never assumes credentials based on a tenant value found in log text. Explicit session
ids outside the selected tenant are refused. The library also accepts an existing tenant
scope and refuses attempts to switch it. Schedule separate invocations for authorized tenants;
the command requires `--tenant` even with SQLite to keep its operational contract consistent.

### 7.5 Caching (`cache.py`)

finbot caches only what may be a little stale and holds no customer record:

| Cached | Key | TTL | Invalidation |
|---|---|---|---|
| Help-page search results | hash of normalised query, k, caller's groups, index generation | 300 s (`FINBOT_KB_CACHE_TTL_S`) | any re-ingest bumps the generation |
| Model breaker health | model name | 1 s, per worker | a worker's own publish replaces its copy |

Never cached: record tools, model answers, session state, tickets, grants, tokens. A record
answer must come from the system of record on every turn; a cached "approved" after a reversal
is the wrong answer the gate exists to stop. For the same reason there is no semantic answer
cache. Cached search results are still screened on every turn.

**Backend choice.** `memory` (default) needs nothing. For a shared cache across pods, `redis`
speaks the Redis protocol, so it works with ElastiCache for Valkey or Redis OSS and MemoryDB.
Valkey on ElastiCache is the recommendation on AWS: managed, in the VPC, IAM auth and TLS,
sub-millisecond reads, and a small client. Couchbase would also work behind the same port, but
its strengths (a document database with SQL++ queries, cross-region replication, mobile sync)
are not needed for two key-value reads with a TTL, and it is one more cluster to operate. A cache
failure is a miss, never an error.

## 8. Deployment

```
client ─HTTPS─► ALB + WAF (per-IP rate rule, TLS) ─► api pods (http_api.py, gunicorn, HPA 2–20 on CPU)
                                                        │ FINBOT_DISPATCH=queue: enqueue to SQS FIFO
                                                        │ (group = session_id), poll for the commit, 202 if slow
                                                        ▼
                                       worker pods (worker_main.py, KEDA on queue depth, PDB)
                                                        │
          DynamoDB (sessions, leases, journal, health, control, guard policy; KMS, PITR, TTL)
          Bedrock (models in two regions, guardrail) · Claude API (last failover target)
          vector index (OpenSearch or pgvector) · systems of record (read-only)
          S3 write-once audit bucket · KMS keys · VPC endpoints for all AWS services
```

- One service account and IAM role per component (api, worker, ingest); no static keys.
- The image installs PyJWT with crypto support and the OpenSearch adapter because the supplied
  deployment enables both. CI builds the image and imports those adapters with networking off.
- `FINBOT_TENANT_ROLE_ARN` is supplied from the stack's `TenantDataRoleArn` output. DynamoDB
  with production JWKS authentication fails at settings construction if the ARN is missing.
  Base pod credentials cannot access session partitions.
- API and worker roles have an additional exact-`TICKETS` grant for `GetItem`, `PutItem` and
  `Query`; no scan, delete or table-wide session access. This is the existing shared support
  desk, whose support/admin roles operate across tenants. It is separate from tenant-isolated
  session data. A tenant-specific support-desk model would require a different ticket contract.
- Namespace with the restricted Pod Security standard; containers non-root with a read-only
  root filesystem.
- Network policy denies by default; workers take no inbound traffic; egress only to DNS and AWS
  endpoints.
- No secrets in the cluster: signing keys come from KMS or the environment of the IAM role.
- Infrastructure is defined in `infra/cdk` (Python); `tests/test_infra.py` asserts its security
  properties on the synthesized template. Kubernetes manifests are in `deploy/k8s`. None of it
  has been applied to a live account or cluster.

## 9. Configuration

Selected settings (`finbot/config.py`; all `FINBOT_*` environment variables):

| Setting | Default | Purpose |
|---|---|---|
| `FINBOT_DOMAINS` | all twelve | which domain plug-ins run |
| `FINBOT_LLM_PROVIDER`, `FINBOT_MODEL_CHAIN` | — | provider and the ordered failover chain |
| `FINBOT_ROUTING`, `FINBOT_FAST_MODEL` | `single` | opt-in cheaper tier for simple turns |
| `FINBOT_REPAIR_ATTEMPTS` | 1 | model repairs before the record-built answer |
| `FINBOT_RECORD_FALLBACK` | on | allow the record-built answer |
| `FINBOT_GUARDRAIL`, `FINBOT_GUARDRAIL_ID`, `FINBOT_GUARDRAIL_VERSION` | local | guardrail provider |
| `FINBOT_STORE_PROVIDER`, `FINBOT_DYNAMO_TABLE` | sqlite | session store |
| `FINBOT_AUTH`, `FINBOT_AUTH_JWKS_URL`, `FINBOT_AUTH_ISSUER`, `FINBOT_AUTH_AUDIENCE` | hmac | token verification |
| `FINBOT_ENCRYPTION`, `FINBOT_KMS_KEY_ID`, `FINBOT_ENCRYPTION_REQUIRED` | — | envelope encryption |
| `FINBOT_RATE_USER_PER_MIN`, `FINBOT_RATE_TENANT_PER_MIN`, `FINBOT_RATE_SCOPE` | 30, 600, auto | rate limits; `auto` counts in the shared store with DynamoDB |
| `FINBOT_TOOL_POLICY` | — | JSON rules for the security gateway: `{id, tool, roles, effect: deny|approve, reason}` |
| `FINBOT_TENANT_ROLE_ARN` | — | required with DynamoDB + JWKS; use `TenantDataRoleArn` stack output |
| `FINBOT_KB_MAX_AGE_DAYS` | 540 | when a cited help page counts as stale |
| `FINBOT_QUEUE_URL` | — | turn queue for workers |
| `FINBOT_CACHE`, `FINBOT_CACHE_URL`, `FINBOT_KB_CACHE_TTL_S` | memory, —, 300 | cache backend (section 7.4) |
| `FINBOT_TELEMETRY`, `FINBOT_TELEMETRY_PATH` | none | AI observability backend (section 10.1) |

Fixed in code: lease 60 s, turn deadline 90 s, 6 tool rounds, 30 turns per thread, handoff after
2 unverifiable answers, control sync 2 s, session idle timeout 30 min and maximum lifetime 24 h.

## 10. Observability

### 10.1 AI observability (`telemetry.py`)

Traces follow the OpenTelemetry semantic conventions for generative AI, so OTel backends
(CloudWatch Application Signals / X-Ray, Grafana Tempo, Datadog, Honeycomb, Langfuse, Arize
Phoenix) show them as LLM traces.

```
finbot.turn            conversation id (hashed), tenant, role, blocked_by, reason codes, repaired,
│                      escalated, tier, guard policy version, tool and model call counts
├─ chat <model>        gen_ai.provider.name, gen_ai.request.model, finish reason, input and
│                      output tokens (when the provider reports them), finbot.failover
├─ execute_tool <tool> gen_ai.tool.name, status, refused_by (provenance or scope), fields screened
└─ finbot.verify       allow or block, reason codes, repaired, records_only
```

**What a span never holds**: the user's text, the model's answer, tool data or record ids.
Attributes are an allow-list (anything else is dropped), and reasons are cut to their code
(`ungrounded_amount`, not `ungrounded_amount:480.00`). Traces therefore stay out of PCI and
privacy scope, and can go to a third-party backend. A test asserts that a turn containing a card
number and a record id leaves neither in any span.

Backends: `FINBOT_TELEMETRY=none` (default), `log` (JSON lines to stdout or
`FINBOT_TELEMETRY_PATH`), `otel` (OpenTelemetry SDK; exporter and sampling from the standard
`OTEL_*` variables).

**AI quality signals to chart and alert on**, all derivable from spans:

| Signal | From | Why |
|---|---|---|
| Block rate by reason code | `finbot.verify` | a rising `status_mismatch` or `unverified_contact` means a model change or an attack |
| Repair success rate | `finbot.repaired` | how often one repair fixes a draft |
| Record-built answer rate | `finbot.records_only` | strictness cost; should stay low |
| Refusal and handoff rate | `finbot.blocked_by`, `finbot.escalated` | customer experience |
| Failover rate per model | `finbot.failover` | a sick primary |
| Tokens per turn, model calls per turn | `chat` spans | cost |
| Tool refusals by provenance | `execute_tool` | injection attempts reaching the model |
| Latency per stage | span durations | where the 90 s budget goes |

### 10.2 Other signals

- **Turn audit log**: one JSON line per turn (section 5), to stdout in containers.
- **Model health**: `engine.status()` per target (breaker state, failures, refusals,
  `served_as_failover`, p50 latency); `models_status` on the control plane.
- **Operations report** (`scripts/ops_report.py`, `opsmetrics.py`): repair, record-answer,
  refusal and handoff rates, model calls per turn.
- **Guard upkeep** (`GuardrailController.upkeep`): hits per rule, idle rules, work waiting on a
  person, labels per 1,000 turns.
- **Alerts worth wiring**: `egress_leak` in DLP records; breaker open on the last strong model;
  dead-letter queue depth; `unavailable` rate; `records_only` and handoff rates.

### 10.3 Capacity and load

`scripts/load_test.py` runs the full worker path (lease, journal, pipeline, tools, gate, commit)
in N processes at once with the scripted model. Measured on an 8-core laptop:

| Run | Throughput | Turn latency p50 / p95 | CPU per turn | Memory per worker |
|---|---|---|---|---|
| 1 worker, no model latency | 265 turns/s | 2.6 / 4.0 ms | 2.3 ms | 42 MB |
| 4 workers, no model latency | 589 turns/s | 3.2 / 11.9 ms | 2.4 ms | 45 MB |
| 8 workers, 2 s per model call | 2.0 turns/s | 4.0 / 4.1 s | 12 ms | 47 MB |

**What it shows.** finbot's own code costs a few milliseconds of CPU per turn. With a real
model, throughput is set by the model, not the code: each worker carries
`1 / (model calls per turn × seconds per call)` turns per second. The third run matches that
(8 / (1.8 × 2) ≈ 2.2).

**How many bots can run.** A worker pod is stateless and holds one turn in flight, so concurrency
is the number of pods, scaled by KEDA on queue depth. Sessions are rows in DynamoDB and cost
nothing while idle. An illustration, with the model latency assumed, not measured:

| Assumption | Value |
|---|---|
| Model calls per turn | 1.9 (measured, scripted model) |
| Seconds per model call | 5 (assumed) |
| Turn time | about 10 s, so one pod carries about 6 turns a minute |
| Active users, one message a minute each | 1,000, so about 17 turns/s |
| Pods needed | about 170 at 250m CPU / 512 Mi each (CPU nearly idle; memory 45 MB used) |
| Model requests | about 1,900 a minute, plus repairs |

The real ceilings are outside finbot: **Bedrock quotas** (requests and tokens per minute, per
model, per region; the two-region failover chain also spreads load), then DynamoDB and SQS
throughput, which scale on demand. Since a pod's CPU is nearly idle while it waits on the model,
running several turns per pod (threads or asyncio) would cut the pod count by that factor; today
it is one turn per pod to keep shutdown simple. None of this has been load-tested on AWS.

## 11. Compliance mapping

finbot is not certified against anything, and this is not legal advice. This table says which
controls exist in code and which obligation each one helps with, so a compliance team has a
starting point.

| Obligation | What it asks, in short | Control in finbot | Gap |
|---|---|---|---|
| PCI DSS (card data) | protect stored cardholder data, never store sensitive authentication data, log access | masking on every path before storage, logs, queues and model; DLP at the edge; `egress_leak` alert; write-once audit for a year | cardholder-data environment scoping and a QSA assessment |
| Accuracy of statements (CFPB on chatbots; UDAAP) | inaccurate answers about a consumer's account can be a violation | the hallucination gate, value provenance, record-built answers, "I won't guess" | live-model evaluation |
| Adverse action reasons (ECOA / Reg B, FCRA) | a credit denial gives specific principal reasons | the bot repeats only the reasons on the record (`decision_reasons`) and blocks guessed ones; credit score never reaches the model | reason wording owned by the lender |
| Anti-tipping-off (BSA/AML) | do not reveal a suspicious-activity review | review reasons are not in the field allow-list; guessed screening causes are blocked | — |
| Privacy (GLBA, GDPR, CCPA) | minimise, protect, retain only as needed, honour erasure | field allow-list; PII masking; envelope encryption; idle session TTL; audit holds categories and counts, not values; traces hold no content | a data-retention schedule and an erasure workflow across sessions, tickets and audit |
| Model risk management (e.g. SR 11-7) | validate, monitor, control changes | evaluation, stress, false-block and red-team suites; guard-rule changes need the gate, shadow, canary and an admin; versioned policies with rollback; AI observability | independent validation |
| Automated-decision and AI transparency (EU AI Act, state laws) | tell people they are talking to an AI; human route | handoff to a person on complaints, possible fraud, hardship, two unverifiable answers in a row, and non-English messages | no explicit "you are chatting with an automated assistant" notice in `replies.py`; a plain "let me talk to a person" is not a built-in trigger (the adaptive demo learns one) |
| Complaints and vulnerable customers | route complaints, fraud victims and people in hardship to people | baseline escalate rules (`adaptive/policy.py`: complaint, unauthorised payment, stolen card or scam, hardship) and tickets | — |
| Record keeping and audit (SOC 2, PCI 10) | tamper-evident logs of who did what | hash-chained audit log, separate audit key, S3 Object Lock | log review process |

## 12. Testing and evaluation

| Suite | What it covers | Result (offline) |
|---|---|---|
| Unit and integration tests | 475 tests; includes the review regressions below | all pass where the production packages are installed |
| Release gate | `scripts/release_check.py`: every gate with fixed thresholds | see [release.md](release.md) |
| Permission changes | same-subject reduced scopes, role/group changes, old-reply replay, equivalent token renewal and fresh-session access | `test_layers.SessionPermissions` |
| Release and retention | overlapping release/commit and renewal, local thread exclusion, real adapter update shape, TTL deletion simulation, fixed session horizon and legacy migration | `test_store_contract`, `test_deployment.ReleaseExclusion` |
| Final-call cancellation | cancellation during the last model response rolls back state and deduplicates, on both stores | `test_cancellation.FinalCallCancellation` |
| Production configuration | enabled adapters import, missing tenant ARN fails early, synthesized ticket permissions exercised through the real adapter | `test_deployment`, `test_infra` |
| Scoped audit | explicit tenant, shared-log filtering, restored context, refused tenant switching and CLI argument validation | `test_reconcile.ScopedAudit` |
| Reconciliation | `tests/test_reconcile.py`: tickets vs records, audit vs commits, index vs source, unknown actions, script exit codes | all pass |
| Multi-process durability | `scripts/durability_test.py`: kill -9 mid-turn, frozen worker, lost reply, 32-turn memory across restarts, model failover, 6-process contention, guardrail outage, killed ingestion | 8/8 |
| Evaluation sets | lookups, not found, tenant isolation, context switches, PII, retry and routing advice, review withholding, domains | 41/41 dev, 185/185 held-out, 192/192 and 184/184 on two unseen data sets |
| Hallucination stress test | 12 kinds of injected errors | 0 of 392 and 0 of 1,867 reached the user; 0 of 3,774 on the unseen sets |
| False-block probe | every failed payment and domain record, both roles | 0 of 2,876 correct answers blocked |
| Consistency | pass^k with half the answers corrupted | pass^16 = 1.000 (dev) |
| Red team | 34 attacks and 7 benign look-alikes, model that obeys any instruction | 0/34 defended, 25/34 undefended; multi-turn 0/5 defended, 4/5 undefended |

Commands: `python -m unittest discover -s tests`, `scripts/run_eval.py [--data DIR]
[--hallucinate]`, `scripts/run_consistency.py --k 16`, `scripts/run_redteam.py`.

## 13. Open issues and risks

| Issue | Impact | Next step |
|---|---|---|
| No live-model evaluation | real wordings may slip past or trip the pattern checks | run the eval, stress test and red team against Bedrock |
| Queue mode not yet run on AWS | the 202-and-resend path depends on real SQS and ALB timing | measure wait times on staging; tune `FINBOT_DISPATCH_WAIT_S` |
| Pattern-based status and cause checks | misses and false blocks on unfamiliar wording | measure on live traffic; consider claim-level checking |
| Help-page answers not checked against the page | a wrong summary of a correct page passes | claim-level check against the cited page |
| Grants signed with HS256 | tool services share a secret | asymmetric KMS signing |
| Self-authored red team | defences not tested against an adaptive attacker | outside red team; mutation-based attacks in the guard gate |
| No hash-pinned lock file | supply-chain drift | pin dependencies with hashes |
| No AI disclosure notice | transparency rules in some jurisdictions | add a fixed opening notice in `replies.py` |
| One turn per worker pod | more pods than CPU needs | concurrent turns per pod |
