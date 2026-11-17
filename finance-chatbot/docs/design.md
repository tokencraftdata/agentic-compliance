# finbot design: layers, patterns, control plane, ingestion

*Updated November 2026. The component-level specification (interfaces, data model, sequences,
resiliency, deployment) is in [technical-design.md](technical-design.md).*

![finbot architecture: trust boundary, turn pipeline, model engine, tools over twelve domain plug-ins](diagrams/finbot-architecture.png)

Everything here is Python. The serving path and the ingestion/data-science path
share one codebase and one set of contracts (`finbot/ports.py`).

**Current state.** Twelve domain plug-ins behind one core; a six-verifier hallucination gate with
one repair and a record-built fallback; argument and value provenance; per-turn delegation grants,
envelope encryption, DLP at the edge; model failover with shared circuit breakers; crash-safe
stateless workers. Verified offline only (scripted models, synthetic data); never deployed, never
run against a live model. More diagrams: [the gate](diagrams/finbot-gate.png),
[a domain plug-in](diagrams/finbot-domain.png), [security](diagrams/finbot-security.png).

## 1. Layers

```
  HTTP (http_api.py) / Lambda / CLI
        │  token
  ┌─────▼──────────────────────────────────────────────────────────────┐
  │ service.py       trust boundary (facade)                           │
  │   verify token → revocation → rate limit → Authorizer →            │
  │   validate input → DLP scan in and out (dlp.py)                    │
  ├────────────────────────────────────────────────────────────────────┤
  │ queue.py         QueueConsumer (SQS / in-memory): at-least-once,   │
  │                  retry_later on outage, DLQ after max_receives      │
  │ runtime.py       Worker: claim lease → load → journal → ask →      │
  │                  heartbeat → CAS commit                             │
  ├────────────────────────────────────────────────────────────────────┤
  │ orchestrator.py  ORCHESTRATION LAYER (one turn)                    │
  │   input guard → focus/state → model loop → HallucinationGate →     │
  │   output guard → escalation → audit                                │
  │ verification.py  Verifier chain + bounded repair                   │
  │ provenance.py    which tool-result fields may vouch for a fact     │
  │ agent_identity   per-turn delegation grant, checked per tool call  │
  │ models.py        model engine: chain, breakers, failover, routing  │
  │ resilience.py    retry_call (jitter, deadline), CircuitBreaker     │
  ├────────────────────────────────────────────────────────────────────┤
  │ control.py       CONTROL PLANE: drain/restore models, routing mode │
  ├────────────────────────────────────────────────────────────────────┤
  │ adapters         llm.py · guardrails.py · tools.py · store.py ·    │
  │                  store_dynamo.py · crypto.py (KMS envelope) ·       │
  │                  ingestion/*                                        │
  └────────────────────────────────────────────────────────────────────┘
  container.py wires it all (composition root); registry.py holds the factories.
```

Rule: arrows point down only. The orchestration layer never imports boto3,
anthropic, sqlite3 or a vector DB client.

## 1b. Domain plug-ins

finbot is a fintech support bot, not a payments bot. Each product area is a plug-in in
`finbot/domains/` — accounts, transfers (ach, rtp, wire), kyc, lending, issuing, onboarding,
disputes, mortgages, bnpl, insurance, subscriptions —
and card-payment acceptance (the processor orchestration of section 9) is just one more,
switched on and off like the rest with `FINBOT_DOMAINS`.

A domain is one frozen dataclass:

| Field | What it fixes |
|---|---|
| `prefix`, `tool`, `id_arg`, `source` | how records are named and fetched (`xfer_…` → `get_transfer(transfer_id)`) |
| `fields` | allow-list: anything else on the record (a review reason, a credit score) never reaches the model |
| `statuses`, `open_statuses` | the words an answer may use; an undecided record may not be called decided |
| `policy` (reason key → `Rule`) | meaning, next step per role, and whether the same request may be sent again |
| `causes`, `cause_phrases` | the plain words people use for each cause, for the reason check |
| `summary` | the record stated by code, every field validated, used when the model's answer fails |

The shared machinery in `finbot/domains/checks.py` runs the same for every domain: a status the
record doesn't have is blocked (any tense for final statuses, present tense for transient ones,
and only with the record itself as the sentence's subject), a cause the records don't carry is
blocked, a reason key that wasn't in this turn's records is blocked, and anti-tipping-off comes
free: the reason for a review is not in `fields`, so the bot can't repeat what it never saw.

Adding an area is one module that builds a `Domain` and calls `register()`; the pipeline,
memory, provenance, tickets, record-built answers and the hallucination gate pick it up with no
further change.

## 2. Patterns and where they live

| Pattern | Where | What it buys |
|---|---|---|
| Ports & adapters | `ports.py`; adapters in `llm.py`, `guardrails.py`, `store*.py`, `ingestion/` | Swap a vendor with config, test offline |
| Factory / registry | `registry.py` (`LLM_PROVIDERS`, `GUARDRAILS`, `STORES`, `EMBEDDERS`, `ROUTING`), `ingestion/__init__.py` (`SOURCES`, `VECTOR_STORES`, `INGEST_EMBEDDERS`) | New provider = one registered function; unknown names fail with the list of known ones |
| Strategy | `RoutingStrategy` (single, tiered), `Verifier`, `Chunker`, embedders, `TokenVerifier` | Change a policy without touching the pipeline |
| Adapter | `ClaudeLLM` (Bedrock and Claude API), `BedrockGuardrail`, `Boto3Dynamo`, `SqsQueue`, `OpenSearchVectorStore`, `PgVectorStore`, `ChromaVectorStore`, `JwksJwtVerifier` | Vendor SDKs stay at the edge |
| Decorator | `ClaudeContextualizer` wraps any chunker; fault injectors (`faults.py`) wrap any LLM | Add behaviour without subclassing |
| Chain of responsibility | `VerifierChain` | Add a hallucination check as one class |
| Template method | `IngestionPipeline.run` | Fixed safe order: screen → chunk → embed → upsert → delete stale |
| Facade | `ChatService` | One entry point owns auth and validation |
| Composition root / DI | `Container` (lazy singletons, `overrides=`) | Tests replace any piece without patching modules |
| Circuit breaker, retry, bulkhead-by-deadline | `resilience.py`, `models.py`, `processors/engine.py` | Fail fast, never hang a user |
| Adapter (processor dialects) | `processors/adapters.py` (`ADAPTERS` registry) | Three response dialects become one canonical code set |
| Strategy + chain (routing rules) | `processors/routing.py` (`RULES` registry, `Router`) | Add or reorder a routing rule without touching the engine |
| Policy object | `processors/cascade.py`, `policy.py` | Money-relevant decisions live in code, not in the model |
| Immutable versioned value | `adaptive/policy.py` (`GuardPolicy`) | Every guard change is a new version; rollback is "make the previous one active" |
| Strategy (proposers, suites) | `adaptive/proposers.py`, `adaptive/gate.py` | Add a way to propose rules, or a regression suite, as one class |
| State machine | `adaptive/controller.py` (proposed → rejected / report / awaiting_approval → shadow → canary → active) | A change can't skip the gate, shadow traffic or an admin |
| Pipeline (ordered stages) | `Chatbot.stages`: risk lock → input guard → language → learned policy → focus → write request → model | The flow is one readable list; each stage ends the turn with a `Reply` or passes; a new check is one method |
| Context object | `TurnContext` (one context variable, `current_turn()`) | Per-turn facts (masked text, policy version, known tickets, PII found) set once and read by verifiers, never threaded through every call |
| Data-flow labelling (value provenance) | `provenance.py` (`trusted_view`, `trusted_blob`, `contact_check`) | Third-party text reaches the model but never counts as evidence for a stated fact |
| Capability token (delegation grant) | `agent_identity.py` (`GrantIssuer`, `TOOL_SCOPES`) | A tool runs only with a short-lived grant for its scope; the model holds no credential |
| Single responsibility | `pii.py` (masking), `replies.py` (fixed wording), `actions.TicketIntake` (request → ticket), `auditlog.py` (where audit lines go) | Each rule lives in one module; the orchestrator only sequences them |

## 3. Robustness map

| Failure | Handled by | Proven by |
|---|---|---|
| Hallucinated amount, status, code, URL, retry advice | `HallucinationGate`: verify, one repair on the strong tier, re-verify against the repair's own tool results, else a record-built answer, else refusal | `run_eval.py --hallucinate` (0 of 2,259 leaked), `test_layers.Gate` |
| Model 429/5xx/timeout | `retry_call` with jitter, per-model breaker, failover to the next target | `test_model_engine`, `test_boundaries` |
| Model refuses | refusal fallback to the next target, output still verified | `test_boundaries.test_refusal_fallback_cannot_bypass_guards` |
| Guardrail service down | fail closed | `test_boundaries` |
| Whole turn too slow | 90 s turn deadline across retries + failover | `test_boundaries.LeaseAndDeadline` |
| Worker crash mid-turn | nothing committed; lease lapses; replacement re-runs from last commit; breaker state shared | `test_store_contract` (both stores), `test_boundaries` |
| Duplicate delivery / client resend | turn journal dedupes by `msg_id` | `test_layers.QueueWorker` |
| Poison message | retried with backoff, then DLQ | `test_layers.QueueWorker` |
| Forged tenant in a queue message | re-checked against the session; DLQ | `test_layers.QueueWorker` |
| Cross-tenant access | tenant from verified token only; tools scope by session tenant | `test_layers.ServiceBoundary`, eval `tenant_isolation` |
| Operator needs to pull a model | control plane drain, applied by every worker within `control_sync_s` | `test_layers.ControlPlaneTests` |

## 4. Control plane (model orchestration)

`ControlPlane` writes, workers read. Config lives in the shared store under
`model_engine`; each engine re-reads it at most every 2 s.

- `status` per target: enabled, breaker state, calls, failovers, refusals, p50.
- `drain(name, reason)` refuses to remove the last enabled strong model.
- `restore(name)` brings a target back; its breaker still has to see a healthy probe.
- `set_routing(single|tiered)` turns the cheaper-model tier on or off. `tiered` needs a fast chain.

Every change needs an `admin` Principal and is audit-logged (who, what, why).
Forcing a breaker closed is deliberately not offered.

## 5. Session store: SQLite → DynamoDB

`STORES.create(FINBOT_STORE_PROVIDER)` picks `sqlite` (one host) or `dynamodb`.
Both pass the same contract test (`tests/test_store_contract.py`). The DynamoDB run
uses `InMemoryDynamo`, which evaluates the real condition expressions.

| Guarantee | DynamoDB primitive |
|---|---|
| one owner per session | conditional Put on owner/lease |
| commit only if nothing changed | `TransactWriteItems`: session META (version, owner, live lease) + turn item |
| dedupe | turn item keyed `T#<msg_id>` |
| idle cleanup | 30-minute idle expiry, capped by a 24-hour `hard_expires`; reads enforce both |
| journal retention | every turn's TTL is the fixed session deadline, so dedupe survives later activity |
| lease release | remove only owner/lease attributes, conditioned on owner, version and lease expiry |
| shared breaker health / control config | `H#<model>`, `C#<key>` items |

Table definition: `store_dynamo.TABLE_DEFINITION` (on-demand, KMS, PITR).

A worker keeps its local in-flight guard until release finishes. Sessions cannot be extended
past their fixed retention deadline; clients start a fresh session then. On upgrade, old
DynamoDB sessions without that deadline are expired rather than risking replay of a deleted
journal entry. SQLite migrates its schema while retaining existing data. See
[technical-design.md, section 5](technical-design.md#5-data-model) for the upgrade contract.

## 6. Security

- `Principal` (subject, tenant, role, groups) comes only from a verified JWT.
  - `hmac` (HS256) for dev.
  - `jwks` (Cognito, Okta or Entra ID via PyJWT) for production.
- The verifier checks signature, `alg`, `exp`, `nbf`, `iss` and `aud`.
- The `Authorizer` is deny-by-default:
  - `customer` (legacy alias `merchant`) and `support` may chat, in their own tenant's sessions only.
  - `admin` may use the control plane only.
- An unknown session and another tenant's session return the same 403, so ids cannot be probed.
- Every send also compares the verified role, groups and scopes with the immutable session
  snapshot. A change requires a new session before any model work or cached-reply replay.
  This keeps earlier privileged history out of a reduced-permission conversation.
- No static AWS keys anywhere. Every boto3 client uses the default credential chain (IAM role).
- Tokens: an HS256 key ring addressed by `kid`, and revocation by `jti` or by subject, seen by
  every pod within two seconds.
- Each turn gets a delegation grant (`agent_identity.py`): user as `sub`, agent as `act.sub`,
  tenant, session, scopes cut to read-only, five-minute expiry. Every tool call verifies it.
- Session state, user text and answers are sealed with AES-256-GCM under a per-value data key
  wrapped by KMS, bound to tenant, session and purpose (`crypto.py`). Session items are reached
  only through credentials for the request's tenant (`tenancy.py`, IAM `LeadingKeys`).
- `dlp.py` masks card data, SSNs, IBANs, routing and account numbers and secrets on the way in,
  and scans every response on the way out; card data found outbound is audited as `egress_leak`.
- Rate limits per verified user and per tenant, counted in the shared store so they hold across pods
  (`ratelimit.py`), on top of the WAF's per-IP rule.
- Details and tests: [security.md](security.md), section 5.

## 6b. Prompt injection

Covered in [security.md](security.md): what reaches the model is screened and spotlighted, and
a successful injection still can't read other tenants, change state, assert false facts, cite
planted links, request secrets or leak the prompt. Red team: 0/34 defended, 25/34 undefended.
Two provenance rules close the paths screening cannot: a record lookup needs an id the user typed
(argument provenance), and an amount, date, code, phone number or email in an answer must come
from a field our own systems set, not from a merchant's descriptor or a processor's log message
(value provenance, `provenance.py`).

## 7. Ingestion → vector DB (Python)

```
  sources                  screen                 chunk                embed               vector DB
  local / s3 /          →  injection → QUARANTINE  →  heading-aware   →  Bedrock Titan v2  →  local JSON
  sharepoint / web         card numbers → masked     (+ Claude context)  or Cohere (batch)    OpenSearch Serverless
                                                                                              pgvector (Aurora)
                                                                                              Chroma
```

The pipeline is incremental by document version. It upserts the new chunks,
then deletes the stale ones, so a document is never missing from the index.
With `--full-sync` it also removes documents that left the source. A failure
on one document is reported and the run carries on.

There is also a managed path, `BedrockKnowledgeBase`. Bedrock's own connectors
(S3, plus the wiki and SaaS connectors of Bedrock Managed Knowledge Base) do the chunking, embedding and vector writes;
`sync()` starts an ingestion job. Either way, the chatbot reads through the same
`KnowledgeSearch` port, selected with `FINBOT_KB_PROVIDER=index|vector|bedrock_kb`.

Retrieval re-ranks vector candidates by exact query-word coverage, because
embeddings blur codes like "51" and "N7". Weak or partial matches return
`not_found`, so the bot says it has no documentation instead of guessing.

```bash
python scripts/ingest.py --source local:data/kb_pages.json --store local:kb_vectors.json --embed hashing
```

## 8. Where Claude is used, and where it is not

| Use | Model | Why |
|---|---|---|
| Answering (tool loop) | Opus on Bedrock, failover chain | Reasoning over tool results |
| Repair after a failed verification | strong tier, always | The cheap path already failed |
| Simple turns (optional, `tiered`) | Sonnet/Haiku | Cost; off by default |
| Chunk context at ingestion | Haiku, low effort, document prompt-cached | Better recall; offline batch |
| Possible next steps | Claude-as-judge `Verifier` for free-text policy answers; LLM-graded eval cases; drafting fixes for docs from the human review queue | Each is one class behind an existing port |

Claude is **not** used for:

- **Verification of facts.** This stays deterministic, against tool results.
- **Authorization or tenant scoping.** This is code.
- **Writing to the knowledge base.** Model answers never become sources; the review queue fixes the source docs.
- **Evidence.** Claude-written chunk context is indexed but never returned as evidence.

## 9. Processor orchestration

finbot explains how a payment was routed across processors; it never routes one. The
routing code lives in `finbot/processors/` so the synthetic data is produced by the same
rules the bot explains, and so those rules are tested on their own.

```
Payment ─► Router (capability → health → cost → local) ─► candidates
              │
              ▼
        for each candidate:  client.authorize ─► adapter.decode ─► canonical code
                             breaker (per processor)            CascadePolicy.decide
              │                                                    │ allowed → next processor
              ▼                                                    ▼ refused → stop
        RoutingTrace (plan + attempts) stored on the transaction ─► bot tools read it
```

| Module | Job |
|---|---|
| `model.py` | Frozen value types: `Payment`, `Attempt`, `RoutePlan`, `RoutingTrace`, `Outcome` |
| `codes.py` | Canonical codes (ISO 8583 style) and the one definition of processor-side faults |
| `adapters.py` | One adapter per dialect: numeric codes (northstar), refusal strings (meridian), HTTP error objects (keystone) |
| `catalog.py` | Capabilities, fees, local countries, incidents; loaded from `processors.json` |
| `routing.py` | Rule chain; each rule records a step only when it changes the candidate list |
| `cascade.py` | May a failed attempt go to the next processor? |
| `engine.py` | `PaymentOrchestrator`: route, attempt, normalise, decide, record |
| `simulator.py` | Seeded simulated processors, issuer model and scenarios |
| `explain.py` | Read side for the bot: trace plus code-computed `cascade_advice`; tenant-scoped health |

**Design choices**

- **Canonical codes, unmapped never guessed.** Every dialect decodes to one code set. An unknown
  response becomes `UNMAPPED` and is never cascaded or retried. A "closest match" mapping is how
  a hard decline gets retried as a soft one.
- **Cascade only what the issuer never saw.** A processor error (rejected before the card
  network) or issuer-unavailable may move to the next processor, up to 3 attempts. Issuer
  declines never cascade: the same issuer answers on every processor, and each attempt counts
  toward card-network reattempt limits. Timeouts never cascade: the first processor may have
  authorised the payment, so cascading risks a double charge.
- **Breakers per processor, on the orchestrator's clock.** Only processor-side faults count
  against a processor. An issuer decline means the processor worked. An open breaker removes
  the processor in the health rule, and the trace says so ("northstar skipped: circuit open").
- **Rule order is a design decision.** Hard constraints first, health second, then cost, then
  local acquiring (a stable sort keeps the cost order within each locality tier).
- **The bot stays read-only.** `get_routing_trace` and `get_processor_health` read stored data.
  Health stats use only the asking tenant's newest 500 transactions; incidents are platform
  facts every tenant may see.
- **Routing claims are evidence-checked.** Processor names, "it cascaded", "processor X was
  down" and "send it to another processor" must match the trace, the logs or the health record
  (`guardrails.routing_check`).

**Simulator** (`python3 scripts/simulate_routing.py`, 2,000 payments per simulated day):

| Scenario | Approval | Cascaded | Note |
|---|---|---|---|
| steady | 80.4% | 44 | 1% processor error rates |
| northstar_outage (2 h) | 80.5% | 50 | failover absorbs it; the breaker limits northstar to 15 failed calls |
| keystone_degraded | 79.6% | 125 | 10 payments have no other eligible processor |
| issuer_outage | 65.2% | 400 | cascading cannot fix an issuer: more attempts, lower approval |

The example rules (attempt limit, which outcomes cascade) are for the synthetic data. Replace
them with your networks' and acquirers' current rules before production.

## 10. Adaptive guardrails

finbot's guardrails improve from reviewed mistakes without training the model and without
letting chat text rewrite the rules. Code in `finbot/adaptive/`.

```
reviewer labels ──► proposers ──► gate ──► shadow ──► canary ──► active
 (review queue)     (rules +      (own      (logged,   (% of      (all
                     regression    cases,    not        sessions)  sessions)
                     cases)        benign,   enforced)
                                   suites)
       ▲                                                              │
       └───────── turn audit: drift per version, shadow divergence ◄──┘
```

**What the policy holds.** A `GuardPolicy` is a versioned, immutable set of learned rules on top
of the built-in guards:

| Rule kind | Runs on | Effect |
|---|---|---|
| `input_block` | user text (same decoded views as the injection screen) | blocked like a prompt attack |
| `escalate` | user text | the turn goes to a person: complaints, unauthorised payments, stolen cards, hardship, bereavement |
| `commitment` | the answer, as a verifier | promises the bot can't keep ("you'll get a refund", "guaranteed") get one repair, then the fallback |
| `risk_threshold` | session risk score | +2 per blocked attack, +1 for probing language, -0.5 per clean turn; at the threshold the session goes to a person and stays locked |

The baseline ships with escalation and commitment rules and is checked against
`data/guard_cases.jsonl`.

**Rules the controller enforces.**

- **Built-in guards are never changed.** The prompt-attack screen, PII masking, evidence checks,
  retry and cascade policy and tenant scoping are code. A false positive on one of them comes back
  as a `report` for engineering, never as an automatic change. Baseline rules change by code
  review only; the controller can remove only rules it learned.
- **Only a reviewer's label starts a change.** Raw chat text never becomes a rule. A label says
  what went wrong (missed attack, missed escalation, broken promise, or a false block); the
  reviewer may highlight the phrase. Without a highlight, a rule needs wording shared by at least
  two labelled messages. Claude may draft a regex (`ClaudeProposer`), with the examples passed as
  data; the draft is validated and gated like any other.
- **The gate checks three things.** The change fixes its own evidence. A new input rule matches
  none of the benign corpus (fair questions from the eval sets and the benign red-team cases).
  Replaying the guard cases, the dev eval, the held-out eval and the red team, no case that passed
  before fails after (case-level, not just the same score). Rules with nested quantifiers, empty
  matches or more than 200 characters are refused.
- **Tightening reaches shadow automatically; nothing reaches users without an admin.** Shadow runs
  on every turn and is logged with whether it would have decided differently. Loosening needs an
  admin approval with a reason, and is re-gated first.
- **Promotion needs shadow traffic.** `promote` refuses until the shadow policy has seen
  `min_shadow_turns` real turns. Canary uses a stable hash of the session id, so a conversation
  never switches policy midway.
- **Every step is audited and reversible.** Labels, cycles, approvals, promotions and rollbacks are
  logged with who, what and why. Earlier versions are kept, so rollback is one call (canary first,
  then shadow, then the previous active).
- **No weight training.** Prompts, retrieval and rules adapt; model weights do not. If you ever
  fine-tune, use only reviewer-approved examples and run the same gate and rollout.
- **Fail closed.** If the stored state can't be read, workers keep the last good policy, or the
  baseline. Guards never switch off.

**Demo:** `python3 scripts/guard_controller.py demo` labels five mistakes, runs a cycle (three
rules pass the gate on 18/18 guard cases, 41/41 dev, 185/185 held-out, 41/41 red team (34 attacks
and 7 benign look-alikes); one false
block on a built-in guard goes to engineering), runs 120 turns with the shadow policy logged
(one turn diverges, as expected), promotes through a 10% canary and rolls back.

## 11. Deployment

How finbot runs as a service on AWS with Kubernetes (EKS). The worker process
(`finbot/worker_main.py`), the container image (`Dockerfile`) and reference manifests
(`deploy/k8s/`) are in the repo; they have **not** been run against a live account or
cluster yet. The HTTP adapter is `finbot/http_api.py` (WSGI, under gunicorn).

**API dispatch.** With `FINBOT_DISPATCH=queue` (the reference config) the API pods do what the
diagram below shows: enqueue the turn, poll the session store for the worker's commit, and answer
`202` with `Retry-After` when it takes longer; they never call a model (`finbot/dispatch.py`,
[decisions.md](decisions.md) ADR-006). `inline` (the default) runs the turn in the API process,
for development and single-box use.

```
 client ──HTTPS──► load balancer ──► api pods (ChatService: verify token, own session?, validate)
                                         │  enqueue {session_id, msg_id, tenant_id, text}
                                         ▼
                         SQS FIFO  (group = session_id, dedup = msg_id) ──► DLQ after 5 receives
                                         │ long poll, one message per pod at a time
                                         ▼
                  worker pods (stateless, any pod, any session) ── Worker.handle
                     claim lease ─ load committed state ─ journal turn ─ run turn ─ CAS commit
                       │            │                                     │
                       │            └─ DynamoDB: sessions, leases, turn journal, breaker health,
                       │                         control config, guard policy
                       └─ Bedrock (model chain across regions, Guardrails), OpenSearch (KB),
                          transaction DB and log search (read-only, tenant-scoped)
```

### 11.1 Access

Two kinds of access, kept apart.

**People and clients.** Every request carries a token from the identity provider (OIDC:
Cognito, Okta, Entra ID), verified against its public JWKS (`JwksJwtVerifier`: signature,
`exp`, `iss`, `aud`). Tenant, role and groups come only from the verified token, never from
the request body. `Authorizer` is deny-by-default:

| Role | May |
|---|---|
| `customer` (alias `merchant`) | chat, only in sessions of its own tenant |
| `support` | chat; read the review queue; label mistakes for the guard controller |
| `admin` | model control plane (drain, restore, routing); guard controller (cycle, approve, promote, rollback) |

An unknown session and someone else's session both return 403, so session ids cannot be probed.
Queue consumers check the tenant again, so a forged queue message cannot cross tenants.

**Workloads.** No static AWS keys anywhere: each Kubernetes service account maps to its own
IAM role (IRSA or EKS Pod Identity), and the cluster holds no secrets (tokens are checked with
public keys).

| Identity | Allowed | Not allowed |
|---|---|---|
| `finbot-api` | verify tokens; read session owner; `sqs:SendMessage` to the turn queue | any model call, the vector index, transaction data |
| `finbot-worker` | `bedrock:InvokeModel` and `ApplyGuardrail` on the listed models; DynamoDB on the session table; receive/delete/change-visibility on the queue, send to the DLQ; read the vector index; read-only transaction and log access | writing the vector index; any write to payment systems |
| `finbot-ingest` | read the help-doc sources; Bedrock embeddings; write the vector index | the session table, the queue, chat models |

### 11.2 A cluster of workers

Workers keep no state in memory between messages. Everything a turn needs is in the shared
store: the committed session, the turn journal, model breaker health, control-plane config and
the guard policy. So any pod can serve any session, pods can be added or removed at any time,
and a rollout is just pods coming and going.

Two workers never run the same session at once. A worker must hold the session's **lease**
(60 s, renewed before every model call and before commit), and the commit is a
**compare-and-swap** on version and lease, so a worker that lost its lease cannot overwrite
newer work. Shared state that is not per-session is "last write wins" with a short cache:
breaker open/closed, drained models (re-read every 2 s), the active guard policy (re-read every
2 s).

How the parts talk to each other. There are no direct calls between workers; everything goes
through shared, durable state, so a pod can die at any point without leaving another one waiting:

| From → to | Channel | Guarantee |
|---|---|---|
| API → worker (intended) | SQS FIFO, grouped by session, deduplicated by `msg_id`; current HTTP path is synchronous (technical design §7.3) | ordered, at-least-once delivery when wired |
| worker → worker (same session) | session lease + CAS commit in DynamoDB | one owner at a time; a late writer is rejected |
| worker → worker (model health) | breaker state in the store | one pod's outage finding protects all pods |
| operator → workers | control config and guard policy in the store, re-read every 2 s | audited; no redeploy |
| worker → people | shared `TICKETS` partition (`DynamoTickets`), exact-partition Get/Put/Query permissions | one ticket per request even when two pods race (conditional put) |
| people → records | `TicketDesk.resolve` with a required note | audited; the bot can't resolve |
| everything → audit | JSON lines with a `stream` field (`turn`, `control`, `guard`, `tickets`), stdout in containers | one log destination, filterable |

**Context length.** A thread rolls over to a fresh one, seeded with the typed state, after 30
turns *or* when the history passes `max_history_chars` (120,000 characters, about 30,000
tokens), whichever comes first. That bounds both the model's context and the stored session;
the DynamoDB store refuses state above 350 KB with `StateTooLarge` rather than failing on
every retry. Tool results are bounded too (log search at most 50 events, record fields at most
300 characters).

### 11.3 Routing

| What is routed | How | Where |
|---|---|---|
| A message to a worker | SQS FIFO, `MessageGroupId = session_id`: one session's turns stay in order, different sessions run in parallel; `MessageDeduplicationId = msg_id` drops client resends | `queue.SqsQueue` |
| A session to a pod | Not routed: no sticky sessions. Whichever pod receives the message claims the lease | `runtime.Worker` |
| A turn to a model | Code chooses the tier (cheap model for simple turns only when an operator enables it; strong model for hard turns and every repair), then the failover chain with a breaker per model, e.g. `us-east-1` then `us-west-2` | `models.ModelOrchestrator`, `control.ControlPlane` |
| A tool call to data | Tenant comes from the session, never from the model; tools are read-only | `tools.ToolBox` |
| A turn to a person | Repeated unverifiable answers, an escalation rule, or a session risk lock | `orchestrator`, `adaptive` |
| A failing message | Retried with backoff; after 5 receives it goes to the DLQ for a person | `queue.QueueConsumer` |
| A payment to a processor | Not done by the bot: it reads routing traces and explains them | section 9 |

Model routing is changed at runtime through the control plane (drain a region, switch tiered
routing on) with an audit record, not by redeploying.

### 11.4 Kubernetes

| Workload | Kind | Scaling | Notes |
|---|---|---|---|
| `finbot-worker` | Deployment + PDB | KEDA on SQS queue depth (2 waiting turns per pod), 2 to 50 pods | one message at a time per pod; no inbound traffic |
| `finbot-api` | Deployment + Service + HPA + PDB | CPU, 2 to 20 pods | trust boundary; never calls a model |
| `finbot-ingest` | CronJob | one run at a time | incremental by document version |

Settings that matter for correctness, not just tidiness:

- `terminationGracePeriodSeconds: 120`, above the 90 s turn deadline plus one 20 s long poll.
  On SIGTERM the worker stops receiving, finishes the turn in hand, and exits
  (`test_sigterm_finishes_the_turn_in_hand_and_leaves_the_rest_queued`).
- **Queue visibility timeout 150 s**, above the turn deadline plus margin. The worker refuses to
  start otherwise; a shorter timeout would hand a healthy slow turn to a second pod mid-turn.
- **One message per poll.** Concurrency comes from pods, so a stopping pod never holds a batch
  it has to abandon.
- **Liveness** is a heartbeat file touched after every poll; readiness does not apply because
  workers take no traffic.
- **Spread across zones**, `maxUnavailable: 0` on rollout, and a PDB so a node drain never
  removes every worker.
- **Restricted pods**: non-root, read-only root filesystem, no capabilities, default seccomp.
- **Network**: default deny; workers accept nothing inbound; egress only to DNS and the VPC
  endpoints for Bedrock, DynamoDB, SQS, STS and OpenSearch.

Outside the cluster: the DynamoDB table (point-in-time recovery on, TTL on idle sessions),
the FIFO queue and FIFO DLQ, the Bedrock guardrail (`infra/create_guardrail.py`), and the VPC
endpoints. A whole-region outage is handled by the model chain for model calls; the store and
queue stay single-region in this design.

### 11.5 Crash recovery mid-turn

A turn only counts once it is committed. Everything before the commit (model calls, tool
calls, the repair attempt, failover) happens in memory and can be thrown away.

```
receive msg ─► claim lease ─► load committed v7 ─► journal "m42 started" ─► model/tools ... ─► renew lease ─► commit v8 + answer ─► ack
     ▲                                                                          │ crash here
     └────────── message reappears after the visibility timeout ◄───────────────┘ nothing committed
```

| Crash point | What happens | Tested by |
|---|---|---|
| Pod killed mid-turn (OOM, node lost, SIGKILL) | Nothing committed, message not acked. It becomes visible again; another pod waits out the 60 s lease, sees the journalled-but-uncommitted turn and re-runs it from v7. Committed once | `test_killed_pod_turn_is_rerun_once_by_another_pod`, `test_1_crash_mid_turn_...` |
| Rollout or scale-in (SIGTERM) | The pod finishes the turn in hand, commits, stops receiving; queued messages go to other pods | `test_sigterm_finishes_...` |
| Crash after commit, before ack or reply | The message is delivered again; the journal already has the answer for that `msg_id`, so it is returned with no model call | `test_2_crash_after_commit_...`, `test_ack_on_commit_and_dedupe_on_redelivery` |
| Old pod comes back after another took over | Its heartbeat sees the new owner and stops; if it still tries, the CAS commit fails with `StaleWrite` | `test_3_late_worker_...`, `test_heartbeat_stops_a_superseded_worker_early` |
| A model or dependency down | Not a crash: retries and failover inside the 90 s deadline; if nothing answers, the turn stays uncommitted and the message is retried later with the same `msg_id` | `test_outage_is_retried_later_then_answered` |
| A message that always fails | Backoff, then the DLQ after 5 receives for a person | `test_forged_tenant_and_poison_messages_are_dead_lettered` |

What the replacement inherits is only the committed record: tool-derived facts, focus,
history and notices. It never inherits the dead worker's half-finished reasoning. Re-running a
turn is safe because every tool is read-only. If write actions (refunds, retries) are added,
they must use the action journal that already exists: record intent with an `op_id` before the
call, pass `op_id` as the provider's idempotency key, and after a crash ask the provider what
happened before trying again (`test_4_unknown_external_action_...`).

The client side: send a stable `msg_id` with every message and reuse it on resend. A resend of
a committed turn returns the stored answer; a resend of an uncommitted one runs it once.

**Retries.** Every retry has a ceiling and spreads out: model calls retry with full-jitter
backoff inside the 90 s turn deadline, then fail over; queue redeliveries use jittered delays
(half to full) so pods that saw the same outage don't return at the same second; a message that
keeps failing goes to the dead-letter queue after 5 receives. A ticket turn re-run after a crash
finds the ticket it already opened (deterministic id) and tells the user about it as new, since
the user never saw the first reply.

**Not done yet:** the HTTP adapter and result delivery to the client (polling or push), a
timer-based lease heartbeat for single model calls longer than the lease, per-tenant rate
limits at the API, and a live run of these manifests.

## 12. Predictable behaviour, card detection, and write requests

**Predictable, not random.** The model's sampling can't be pinned: on Claude Opus 5.5 the API
rejects `temperature` and `top_p`, and thinking is always on. So finbot does not rely on the
model for anything that must be the same every time. Those decisions are code, with fixed
replies, decided before any model call:

| Decision | Made by | Same input, same result? |
|---|---|---|
| Who is asking, which tenant, which data | verified token, tenant-scoped tools | yes |
| Card data in the message | `mask_pii`, then a fixed notice to the user | yes |
| Prompt attack, denied topic, escalation to a person | input guard, guard policy rules | yes |
| A request to change something | `actions.detect`, then a ticket and a fixed reply | yes |
| Is a fact in the answer true; is retry or cascade advice allowed | evidence checks against the records | yes |
| The wording of an answer | Claude (effort set explicitly, `medium` by default) | facts yes, wording may vary |

The model writes sentences; it does not decide policy, and anything it writes is checked
before the user sees it.

**Card detection.** Card numbers (any common separator, Luhn-valid or card-shaped), track data,
security codes, expiry dates and PINs are masked before the text reaches anything else (see
`docs/security.md` 4a). When a message contained card data, the reply starts with a fixed
line: *"I removed card details from your message. Please don't share card numbers, security
codes or PINs here: we never need them."*

**No write actions.** finbot has no tool that changes anything. When a user asks it to change
something (refund, cancel, retry, unblock, update details, change a limit, waive a fee, close an
account), code:

1. recognises the request before any model call (`finbot/actions.py`); a question about how
   something works ("how do refunds work?", "refund policy?") is not a request;
2. looks the transaction up for this tenant; someone else's transaction gets "not found" and no
   ticket;
3. opens a ticket (`finbot/tickets.py`) with the masked request, the verified facts at that
   moment and, for retries, the retry policy ("05 is not retryable");
4. replies with a fixed template: the ticket id, that a person will do it, and that **nothing has
   changed yet**.

The ticket id is derived from tenant, session, kind and transaction, so asking twice, or a turn
re-run after a crash, never opens a second ticket. People work tickets through `TicketDesk`
(support or admin only, audited): list, claim, then resolve as `done` or `rejected` with a note
saying what they did in the real system. The bot never resolves or executes a ticket, and the
evidence check blocks any answer that names a ticket the session doesn't have or says "I've
raised a ticket" when none exists. Replace `InMemoryTickets` / `JsonFileTickets` with your
ticketing system (Jira, ServiceNow, Zendesk) behind the same three store methods.

## 13. Ideas taken from research

A review of recent papers (all citations checked) produced eight candidates. Four fit finbot
and are built, each with a test (`tests/test_research.py`):

| Paper | Idea | What finbot does | Result |
|---|---|---|---|
| Debenedetti et al. 2025, *Defeating Prompt Injections by Design* (CaMeL), arXiv 2503.18813; Beurer-Kellner et al. 2025, *Design Patterns for Securing LLM Agents against Prompt Injections*, arXiv 2506.08837 | Contain injection by structure: untrusted data must not decide control flow | **Argument provenance**: a tool may be called only with a transaction id the user typed (`Chatbot._provenance`, `SessionMemory.user_ids`). An id first seen inside a record is refused | Two pivot attacks ("also look up txn_X") pass screening in every configuration; provenance alone stops both |
| Same paper, its data-flow half | Every value carries its source; policy decides where it may flow | **Value provenance**: third-party text (merchant names, descriptors, log messages) reaches the model but cannot vouch for an amount, date, code or contact in the answer (`finbot/provenance.py`) | A plain-looking planted refund amount, date, phone number or email passes screening; all five such answers were allowed before and are blocked now, with 0 new false blocks in 2,876 correct answers |
| Russinovich et al. 2025, *Crescendo*, USENIX Security, arXiv 2404.01833; Li et al. 2024, *LLM Defenses Are Not Robust to Multi-Turn Human Jailbreaks*, arXiv 2408.15221 | Attacks that escalate slowly beat single-turn defences | **Multi-turn red team** with detection latency: the first turn stopped against the harmful turn (`data/redteam_multiturn.jsonl`) | 0 of 5 succeed defended, all stopped at the harmful turn; found and fixed a cross-sentence negation bug in the credential check |
| Yao et al. 2024, *tau-bench*, arXiv 2406.12045 | pass^k: an agent must succeed on every one of k runs, not once | **Consistency eval** (`scripts/run_consistency.py`): each case runs k times against a seeded model that corrupts half its answers | pass^16 = 1.000 dev, pass^8 = 1.000 held-out. The first version mis-scored a correct later turn as a leak; markers are now checked per turn and seeds are stable across processes |
| Laban et al. 2025, *LLMs Get Lost in Multi-Turn Conversation*, arXiv 2505.06120 | Models lose track of goals spread over turns | **Code-built recap** at thread rollover: the user's own earlier requests (never a model summary) carried into the fresh thread | The new thread's state lists what the user asked before |

Considered and not built, or built differently:

- **Masking old tool results mid-thread** (Lindenbauer et al. 2025, *The Complexity Trap*,
  arXiv 2508.21433). Editing earlier messages breaks prompt caching and Claude Opus 5.5's
  preserved-thinking history check, so finbot applies the idea only at rollover (fresh thread,
  typed state, recap) and bounds the thread by size.
- **Claim-level abstention** (Mohri & Hashimoto 2024, *Conformal Factuality*, ICML): keep verified
  sentences and drop the failing ones instead of a full fallback. Worth doing next; it changes
  user-visible answers, so it needs its own eval.
- **Adaptive attacker in the guard gate** (Nasr et al. 2025, *The Attacker Moves Second*, arXiv
  2510.09023): mutate blocked attacks (paraphrase, homoglyphs, base64, split across turns) and
  count survivors per candidate rule set, so learned rules don't overfit.
- **Policy checks on tickets** (Zwerdling et al. 2025, *ToolGuard*, EMNLP Industry): refund only on
  a settled charge, amount not above captured, inside the window; results attached to the ticket.
- **Failure codes** (Cemri et al. 2025, *Why Do Multi-Agent LLM Systems Fail?*, NeurIPS D&B): tag
  fallbacks, handoffs and dead letters with a failure code for offline analysis.
- Rejected: full CaMeL interpreter and FIDES (too large for a read-only bot), MELON (re-runs the
  model per tool step, too slow for the 90 s deadline), LLM-judge step monitors (weak against
  adaptive attacks), MiniCheck (needs a self-hosted model).

## 14. Known drawbacks and their mitigations

A strict, checked bot has costs. Each one below has a mitigation in code and a part that stays open.

| Drawback | Mitigation | Where |
|---|---|---|
| The checker reads numbers, codes and statuses, not a wrong reason in plain words | `ReasonVerifier` and `DomainVerifier`: a failure key maps to one cause, a cause to its phrases; an answer affirming a cause the turn's records don't carry is blocked, in every domain | `finbot/reasons.py`, `finbot/domains/checks.py` |
| Strictness means refusals and handoffs | After a failed repair the gate asks for a record-built answer: validated fields and code-table wording only, verified by the same chain. It counts as an answer, so it does not move the session towards a handoff | `finbot/recordanswer.py`, `HallucinationGate.run(fallback=...)` |
| A repair is a second model call | `FINBOT_REPAIR_ATTEMPTS=0` goes straight to the record-built answer. The audit log carries what is needed to price the trade | `finbot/opsmetrics.py`, `scripts/ops_report.py` |
| Rules and labels need upkeep | `GuardrailController.upkeep`: hits per rule, idle learned rules, work waiting on a person, labels per 1,000 turns. Retiring a rule is a loosening and still needs an admin | `finbot/adaptive/controller.py` |
| Only as good as the sources | An audit that fails ingestion on stale pages and on pages that disagree with the retry and code tables; a note added by code when an answer cites an old page | `finbot/freshness.py`, `scripts/kb_audit.py` |
| English only | Stage 2b sends non-English messages to a person with a fixed reply in that language; `LanguageVerifier` blocks non-English answers, which the other verifiers could not read | `finbot/language.py` |
| Unproven live | Not fixable offline. Eval, stress test and red team take a live provider through one environment variable | `scripts/run_eval.py` |

What stays open: the reason check is a phrase table, help-page questions have no record to fall back on, a wrong page that the tables don't cover is still repeated, and none of this has met live traffic.
