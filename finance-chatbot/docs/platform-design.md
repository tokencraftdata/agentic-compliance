# Platform designs: agent orchestration and multi-tenancy

*November 2026.* Two system designs that generalise what finbot does for one support bot to a
platform that runs many agents for many teams. Each section says what finbot **already has**
(with the code), and what a platform would **add**. Nothing marked "add" exists in the repo.

- Part A: a distributed AI agent orchestration platform
- Part B: a multi-tenant AI platform

---

## Part A. Distributed AI agent orchestration platform

Many agents run long workflows: several LLM calls, tool calls, checks, waits for a person, and
sometimes actions with side effects. Workflows last seconds to days and must survive crashes,
redeploys and dependency outages.

### A1. Architecture

```
 clients ──► API (auth, admission, idempotency key) ──► Kafka: workflow.commands  (key = workflow_id)
                                                              │
                         ┌────────────────────────────────────┼─────────────────────────────┐
                         ▼                                    ▼                             ▼
                 orchestrator pool                    LLM step pool                  tool step pool
            (decides the next step;            (model calls; sized to the       (external APIs; one
             owns the workflow lease)           model quota; failover chain)    bulkhead per dependency)
                         │  step tasks ──► Kafka: steps.llm / steps.tool / steps.verify
                         │  ◄── results ── Kafka: step.results  (key = workflow_id)
                         ▼
        state store: workflow row (version, lease, status, deadline), step journal, outbox,
                     idempotency table, action intents, cancel flags          (DynamoDB or Postgres)
                         │
        timers: Kafka retry topics (5 s, 1 min, 10 min) + a scheduler for long waits (hours, days)
        dead letters: Kafka *.dlq topics, with the workflow marked "needs attention"
```

The orchestrator is the only component that changes workflow state. Step workers are
stateless: they take a task, do one thing, and publish a result. This split is what lets each
pool scale on its own bottleneck (model quota, a partner API's rate limit, CPU).

**finbot today** runs one workflow shape (a support turn) in one worker: `runtime.Worker` owns
the session, `orchestrator.Chatbot` decides the steps, and model and tool calls happen inline.
The queue is SQS FIFO behind the `MessageSource` port (`queue.py`); a Kafka adapter would sit
behind the same port.

### A2. Workflow state management

| Concern | Design | finbot today |
|---|---|---|
| Source of truth | one workflow row: `status`, `version`, `owner`, `lease_until`, `deadline`, current step, typed state | session META item: version, owner, lease, sealed state (`store.py`, `store_dynamo.py`) |
| History | append-only step journal: `W#<id> / S#<n>` with input hash, status, result, attempts | turn journal `T#<msg_id>` (`begin_turn`, `commit_turn`) |
| Commit | compare-and-swap on `version` and live lease, in one transaction with the step result and the outbox row | `commit_turn` in one transaction (`TransactWriteItems`) |
| Trust | state holds facts from tools, never model prose; a takeover trusts only committed state | `memory.SessionMemory` facts carry `source`; `load_session` |
| Size | snapshot every N steps; old steps to S3 | thread rollover at 30 turns or a size cap |

Event sourcing alone (rebuilding state from events) is attractive but makes every read a replay;
a versioned snapshot plus an append-only journal gives the audit trail and cheap reads.

### A3. Kafka-based asynchronous execution

| Topic | Key | Purpose |
|---|---|---|
| `workflow.commands` | workflow_id | start, user message, cancel, resume |
| `steps.llm`, `steps.tool`, `steps.verify` | workflow_id | one task per step |
| `step.results` | workflow_id | results back to the orchestrator |
| `retry.5s`, `retry.1m`, `retry.10m` | workflow_id | delayed retries (consumer pauses until the due time) |
| `*.dlq` | workflow_id | poison tasks after N attempts |
| `audit.events` | tenant_id | turn and step records for analytics and alerting |

- **Ordering.** Keying by `workflow_id` puts one workflow's messages on one partition, in order,
  the same guarantee SQS FIFO gives finbot with `MessageGroupId = session_id`.
- **Delivery.** Kafka gives at-least-once to consumers. Its exactly-once mode covers
  read-process-write *within Kafka* only; an LLM call or a partner API is outside it. So every
  consumer is idempotent (A6), and state changes and emitted events go together through an
  **outbox**: the orchestrator writes the next step into an outbox table in the same transaction
  as the state commit, and a relay publishes it.
- **Rebalances.** When partitions move, two consumers can briefly process the same workflow.
  Partition ownership is not a lock, so the workflow lease and the version check (A7) still
  decide who may commit.
- **Back-pressure.** Consumer lag is the scaling signal (KEDA's Kafka scaler), and the admission
  layer refuses new work per tenant when lag passes a threshold (B8).

### A4. Worker pools

| Pool | Bound by | Sizing | Isolation |
|---|---|---|---|
| Orchestrator | state-store writes | small; CPU-light | none needed |
| LLM | model quota (requests and tokens per minute, per region) | concurrency = quota ÷ tokens per call; several calls per pod with asyncio, since a pod mostly waits | per-tenant concurrency caps; tier routing to cheaper models |
| Tool | each partner API's rate limit | one bulkhead (semaphore) per dependency | per-dependency circuit breaker |
| Verify | CPU | a few pods | none |

finbot measured its own code at about 2.4 ms CPU and 45 MB per worker per turn
(`scripts/load_test.py`); the model sets throughput. finbot runs one turn per pod; an LLM pool
should run many concurrent calls per pod.

### A5. Retry and recovery

| Failure | Handling | finbot today |
|---|---|---|
| Transient (429, 5xx, timeout) | retry with full-jitter backoff, inside the step's deadline | `resilience.retry_call`, `is_transient` |
| Dependency keeps failing | circuit breaker per dependency, state shared by all workers | `CircuitBreaker` with shared health (`models.py`, cached by `CachedHealth`) |
| Model refuses or fails | next target in a failover chain; output verified the same way | `models.py` failover and refusal fallback |
| Permanent (400, auth, validation) | no retry; step fails; workflow goes to "needs attention" | 400 and auth errors are raised, not failed over |
| Poison task | N attempts through retry topics, then DLQ | SQS: dead letter after 5 receives (`queue.py`) |
| Worker crash | lease expires; another worker resumes from the last committed step; only the uncommitted step re-runs | `Worker` lease + `recover()`; read-only tools make re-running safe |
| Side effect with unknown outcome | intent recorded before the call; on recovery, ask the provider before any retry | `begin_action` / `finish_action` / `reconcile`; outcome `unknown` is never re-sent blindly |
| Whole platform slow | workflow and step deadlines stop retries; the user gets "try again" | 90 s turn deadline across retries and failover |

### A6. Idempotency

Three keys, at three levels:

1. **Request key** from the client (`msg_id`), reused on resend. The API and the orchestrator
   dedupe on it. finbot: `begin_turn` returns the stored reply for a known `msg_id`, with no
   model call.
2. **Step key** = hash(workflow_id, step number). A step worker that sees a step key already
   completed returns the stored result. Retries reuse the key; only a new step gets a new one.
3. **Operation key** (`op_id`) passed to the provider as its idempotency key for any side effect
   (a refund, a ticket in an external system). finbot: `begin_action(op_id, ...)`.

Plus the outbox (A3) so "state committed" and "next step published" cannot diverge, and a
consumer-side dedupe table keyed by (topic, key, step key) with a TTL.

### A7. Distributed locks and leases

- **Lease, not lock.** A workflow has an owner and a `lease_until`. The owner renews it on a
  heartbeat; a crashed owner's lease simply lapses. finbot: `claim()` before each model call
  and before commit, `LeaseHeld` while another live owner exists.
- **Fencing.** A paused worker (GC, network partition) can wake after its lease is gone. Every
  write carries the version it read; the store rejects stale versions. finbot: `StaleWrite` on
  version or lease mismatch, tested with a late worker.
- **Where the lease lives.** In the same store as the state, with conditional writes (DynamoDB
  condition expressions, Postgres `UPDATE ... WHERE version = ?`). A separate lock service adds a
  second source of truth. Redis-based locks (Redlock) are fine for efficiency but not for
  correctness without fencing.
- **Timing rules** (finbot's, generalised): the queue's visibility timeout or Kafka's max poll
  interval must exceed the step deadline; the lease must be renewed more often than it expires;
  the step deadline sits inside the workflow deadline.

### A8. Cancellation and timeouts

**Built in finbot (this change):** `POST /v1/sessions/{id}/cancel {msg_id}` (`service.cancel`,
`Worker.cancel`). It writes a cancel flag; the worker checks it at every heartbeat, i.e. before
each model call, and also before starting. A cancelled turn commits the *pre-turn* state with a
fixed answer, so a resend of the same `msg_id` returns "cancelled" instead of running again, and
nothing the cancelled turn learned is kept. Tests: `tests/test_cancellation.py` (before start,
mid-turn, resend, DynamoDB store, owner-only, HTTP route).

**For a platform, add:**

| Need | Design |
|---|---|
| Cancel a whole workflow | `cancel` command on `workflow.commands`; the orchestrator marks the workflow `cancelling`, stops scheduling, and publishes cancel to child steps |
| Stop a step in flight | step workers check the flag between calls; an LLM call is abandoned at the next token boundary if streaming, otherwise finishes and is discarded |
| Side effects already done | compensation steps (a saga): each side-effecting step declares its undo, run in reverse order |
| Timeouts | nested: per call (seconds), per step (tens of seconds), per workflow (minutes to days); each retry delay stays inside the enclosing budget |
| Long waits (approval, a partner callback) | durable timers in the scheduler, not sleeping workers; the workflow is parked and costs nothing |

### A9. Build on Kafka, or use a workflow engine

Temporal (or AWS Step Functions) already provides durable state, timers, retries, cancellation,
sagas and versioned workflow code; the orchestrator above re-implements those. Use a workflow
engine when workflows are long, branchy and change often. Build on Kafka when you already run it,
need very high fan-out, and workflows are short and uniform, as finbot's turns are. A hybrid is
common: a workflow engine for orchestration, Kafka for events and analytics.

### A10. What finbot has, what to add

| Capability | finbot | Add for the platform |
|---|---|---|
| State with CAS commit and journal | yes | per-step journal, snapshots, outbox |
| Async execution | SQS FIFO | Kafka adapter behind `MessageSource`; retry topics; scheduler |
| Worker pools | one pool, one turn per pod | separate LLM / tool / verify pools, concurrent calls per pod |
| Retry, breakers, failover, deadlines | yes | per-dependency bulkheads |
| Idempotency | request key, operation key | step key, consumer dedupe table |
| Leases and fencing | yes | — |
| Cancellation | yes, per turn | per workflow, with compensation |
| Long waits and human approval | no | durable timers |

---

## Part B. Multi-tenant AI platform

Several security teams share the same AI infrastructure. No team may see another's data,
agents, prompts, credentials or results, and one team's load must not slow the others.

### B1. Tenant model and isolation tiers

```
tenant ─┬─ teams ── users (roles)
        ├─ agents (definitions, prompts, tool grants)
        ├─ credentials (to the tenant's own systems)
        ├─ data (sessions, documents, vectors, tickets, audit)
        └─ quotas (requests, tokens, concurrency, storage)
```

| Tier | Compute | Data | Keys | When |
|---|---|---|---|---|
| Pool | shared pods | shared tables, partitioned by tenant | shared key, tenant in the encryption context | most tenants |
| Bridge | shared pods | own vector index, own S3 prefix and topics | own KMS key | sensitive data |
| Silo | own pods or account | own tables | own key, or the tenant's (BYOK) | regulated or contractual |

### B2. Data partitioning

| Store | Pool-tier partitioning | finbot today |
|---|---|---|
| Sessions and state | partition key starts with the tenant | **done**: session ids are `<tenant>.<uuid>`, so keys are `S#<tenant>.<uuid>`; the store refuses an id naming another tenant |
| Records via tools | tenant from the session, passed by code, never by the model | yes: tools scope by session tenant; schemas have no tenant field |
| Vectors | one index or namespace per tenant, or a mandatory tenant filter applied by code | one shared help-page index, filtered by group; it holds no customer data |
| Objects | `s3://bucket/<tenant>/...`, bucket policy by prefix | audit bucket only |
| Kafka | tenant in the key and a header; per-tenant topics for bridge and silo | — |
| Cache | tenant in every key | help-page search cache keyed by groups (shared docs only); records never cached |
| Logs and traces | tenant attribute, no content | audit and spans carry tenant; spans hold no content (`telemetry.py`) |

**Done: enforcement below the application** (`finbot/tenancy.py`, `infra/cdk`). The API sets the
request's tenant from the verified token, and a queue worker from the message (already checked
against the session). With `FINBOT_TENANT_ROLE_ARN` set, every call on a session item goes
through credentials from assuming `finbot-tenant-data` with the session tag `tenant_id` = that
tenant; the role may only touch keys matching `S#${aws:PrincipalTag/tenant_id}.*`
(`dynamodb:LeadingKeys`). The pod roles may only touch shared items (`H#`, `C#`, `R#`). So another
tenant's session is refused twice: in code, because the key names a different tenant than the
request, and in IAM. The tag comes from the request, never from the key being read; a tag taken
from the key would only compare a session with itself. Still to add: the same for S3 prefixes,
once tenant documents are stored.

### B3. Tenant-scoped authorization

- **Tenant from the verified token only.** finbot: `Principal` comes from a verified JWT; the
  request body has no tenant field; a forged tenant in a queue message is dead-lettered.
- **Deny by default, same answer for "not yours" and "not found".** finbot: `Authorizer`, one 403
  for both, so session ids cannot be probed.
- **Per-call delegation grants.** finbot: each turn mints a grant (user as `sub`, agent as
  `act.sub`, tenant, session, scopes, five-minute expiry); every tool verifies it for its scope,
  and a remote tool service can verify it too (`agent_identity.py`).
- **Agents as tenant resources.** Add: agent definitions, prompts and tool grants stored under
  the tenant, versioned, and editable only by that tenant's admins. An agent can call only tools
  granted to it, and only with its tenant's grant.

### B4. Credentials

Agents never hold credentials. finbot already keeps them out of the model: tools run with
code-held access, after a grant check. For a platform:

- each tenant's secrets in a secrets manager under a tenant path, readable only by a role whose
  session tag matches the tenant;
- a **credential broker** in the tool layer injects the secret into the outbound call after the
  grant check, so neither the model nor the agent definition ever sees it;
- short-lived, scoped credentials (OAuth token exchange to the tenant's systems) over long-lived
  keys;
- rotation and revocation per tenant; finbot's token revocation by `jti` or subject is the
  pattern.

### B5. Encryption

| Layer | Design | finbot today |
|---|---|---|
| At rest | envelope encryption, data key per value, wrapped by KMS | yes (`crypto.EnvelopeCipher`, AES-256-GCM) |
| Context binding | the encryption context names the tenant, session and purpose; a value copied to another tenant's row will not decrypt, and CloudTrail shows whose data each decrypt was for | **done**: `fbenc2:` envelopes bind tenant, session and purpose; older `fbenc1:` values still open; the prefix cannot be downgraded |
| Keys | shared key for pool, own key for bridge and silo, BYOK for silo | one key, purpose-restricted key policy |
| Offboarding | crypto-shredding: schedule deletion of the tenant's key, and every copy is unreadable, backups included | — |
| In transit | TLS everywhere; VPC endpoints to AWS services | ALB TLS, VPC endpoints in `infra/cdk` |

### B6. Resource quotas

| Quota | Enforced at | finbot today |
|---|---|---|
| Requests per minute, per user and per tenant | API admission | **done, shared**: `SharedRateLimiter` counts in the session store (DynamoDB atomic `ADD`), a sliding one-minute window, exact across pods; default with DynamoDB (`FINBOT_RATE_SCOPE`) |
| Model tokens per minute and per day, per tenant | a model gateway in front of the LLM pool | — |
| Concurrent workflows and concurrent model calls, per tenant | orchestrator admission and LLM pool | — |
| Storage (sessions, documents, vectors) | write path | session size cap (DynamoDB item limit) |
| Tool calls per partner API, per tenant | tool bulkheads | — |

Quotas need a shared counter to be exact across pods; finbot's request limits now use one
(DynamoDB atomic counters, one key per window). Token and concurrency quotas can reuse it.

### B7. Noisy-neighbor protection

1. **Admission control.** Refuse at the edge (429 with `Retry-After`) before work is queued, per
   tenant, using the quotas above.
2. **Fair scheduling.** One shared queue lets a burst from one tenant delay everyone. Options:
   per-tenant partitions or topics with a weighted round-robin consumer; or deficit round robin
   over per-tenant queues in the orchestrator. Weight by tier.
3. **Bulkheads.** A per-tenant cap on concurrent model calls and tool calls inside each pool, so
   one tenant can use at most its share even when the pool has spare capacity briefly.
4. **Per-tenant circuit breakers** on the tenant's own systems, so a tenant whose backend is down
   does not hold workers waiting on timeouts.
5. **Model capacity.** Separate provisioned throughput for premium tenants; spread pooled tenants
   across regions through the failover chain.
6. **Observability per tenant.** Latency, lag, tokens and errors by tenant, so the noisy one is
   visible; finbot's spans and audit records already carry the tenant.

### B8. Cross-tenant data leakage

| Path | Control | finbot today |
|---|---|---|
| A tool returns another tenant's record | tenant from the session; another tenant's record is "not found" | yes; eval cases `tenant_isolation` and `cross_tenant` |
| The model is steered to fetch other records | argument provenance: ids must come from the user | yes |
| Model context mixes tenants | one session is one tenant; each turn's context is that session only; no shared memory across sessions | yes |
| Retrieval returns another tenant's document | per-tenant index or a code-applied tenant filter | shared help pages only; add per-tenant indexes for tenant documents |
| Cache returns another tenant's entry | tenant in every cache key; never cache records | records never cached; help-page keys include groups |
| Prompt caching shares a prefix | keep tenant data out of shared prefixes; the system prompt is shared, tenant content comes after the cache point | the system prompt holds no tenant data |
| Logs, traces, metrics leak content | no content in telemetry; tenant tag on every record | yes (`telemetry.py` allow-list; audit masks) |
| Fine-tuning or learned rules carry one tenant's data to another | learn per tenant, or only from reviewed, scrubbed examples | learned guard rules come from reviewer labels; they are regexes, not tenant data |
| Credentials reach the wrong tenant | broker injects per tenant after the grant check | grants carry the tenant |

**Detection.** Plant a unique canary string in each tenant's data; alert if it ever appears in
another tenant's output, cache, log or trace. Run cross-tenant cases in every evaluation, as
finbot does with `tenant_isolation`.

### B9. What finbot has, what to add

| Capability | finbot | Add for the platform |
|---|---|---|
| Tenant from verified token, deny by default | yes | — |
| Per-call grants with tenant and scope | yes | agent definitions and tool grants as tenant resources |
| Data partitioning | tenant-first keys; tenant-scoped credentials with IAM `LeadingKeys` | S3 prefix conditions for tenant documents |
| Encryption | per-value envelope bound to tenant, session and purpose | per-tenant keys; crypto-shredding |
| Credentials | out of the model | per-tenant secrets and a broker |
| Quotas | requests per user and tenant, shared across pods | token and concurrency quotas on the same counters |
| Noisy neighbor | rate limits | fair scheduling, per-tenant bulkheads and breakers |
| Leakage tests | tenant-isolation evals | canary strings and alerts |
