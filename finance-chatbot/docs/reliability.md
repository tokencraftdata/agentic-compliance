# Crash recovery, session store and hallucination control

How finbot keeps a conversation correct when a worker dies, when another worker takes over, and when the model makes things up. Every claim here is backed by a test in `tests/test_reliability.py` or by `scripts/run_eval.py --hallucinate`.

## 1. Session store (`finbot/store.py`)

One durable record per session. A conversation summary alone is not enough: the record holds the actual case state.

| Stored | Why |
|---|---|
| Typed ledger per transaction (tool-derived facts, superseded history), focus, pending notices | What a replacement worker must inherit |
| Append-only message history (SDK blocks serialised as JSON, thinking signatures included) | The model sees the same conversation after takeover; prompt cache still works |
| `version` | Compare-and-swap on every commit |
| `owner`, `lease_until` | Which worker may write right now |
| Turn journal keyed by client `msg_id`: `started` → `committed` | Detect half-finished turns; answer each message once |
| Action journal keyed by durable `op_id`: `pending` → `succeeded`/`failed`, or `unknown` after a crash | Side effects are never blindly repeated |
| `expires` (idle TTL, default 30 min) | Abandoned sessions don't live forever |

SQLite keeps it runnable anywhere. In production use **DynamoDB** (conditional writes on `version` and `owner`, a TTL attribute) or **Postgres** (a version column in the `UPDATE ... WHERE`). The method contract stays the same.

## 2. Crash recovery (`finbot/runtime.py`)

Workers are stateless; any worker can serve any session.

```
claim lease ─► load committed state ─► journal turn (dedupe msg_id) ─► run turn in memory ─► commit (CAS on version + lease)
```

| Takeover step | Implementation |
|---|---|
| Save a shared case record | `commit_turn` writes the state and marks the turn committed in **one transaction** |
| Detect the interruption, assign a replacement | Lease expiry. `claim()` fails with `LeaseHeld` while the old lease is live and succeeds after it expires. In production a workflow engine (Temporal, Step Functions) or the load balancer routes the retry |
| Controlled ownership | The new owner holds the lease. A late write from the old worker fails with `StaleWrite` (version or lease mismatch), so it cannot overwrite newer work |
| Rebuild trusted context | The replacement loads only committed state. Facts in the ledger come only from tool results, never from model prose. Document text and earlier model output gain no authority from the handoff |
| Resume the unfinished step safely | `recover()` lists journalled-but-uncommitted turns. They are re-run from the last commit, which is safe because every tool is read-only. Committed turns are never redone |

### Crash points tested

| # | Crash | Expected behaviour | Test |
|---|---|---|---|
| 1 | Mid-turn: tool results received, nothing committed | Version unchanged. The replacement waits out the lease, sees the incomplete turn and re-runs it (`attempts=2`). Earlier committed facts are kept | `test_1_crash_mid_turn_...` |
| 2 | After commit, before the client got the reply | The client resends the same `msg_id` and gets the stored answer. **No model call**, no new version | `test_2_crash_after_commit_...` |
| 3 | During handoff; the old worker comes back late | The old worker's commit is rejected (`StaleWrite`). The new worker's state survives | `test_3_late_worker_...` |
| 3b | Follow-up after takeover | "Can I retry it?" resolves to the transaction committed by the dead worker | `test_3b_follow_up_...` |
| 4 | External action started, outcome never recorded | Marked `unknown` on takeover. `begin_action` with the same `op_id` returns `unknown`, so it is not re-sent. `reconcile()` asks the system of record first | `test_4_unknown_external_action_...` |

**Payments caveat.** A checkpoint alone does not make an external action happen exactly once. Record intent (`begin_action`) before calling the provider. Pass `op_id` as the provider's idempotency key. After a crash, check the provider before any retry. finbot is read-only today; this path is built and tested so that refund/retry tools can be added safely later.

### Try it

```bash
python3 -m finbot.app
```

Kill the process, then resume with the printed session id:

```bash
FINBOT_SESSION=<id> python3 -m finbot.app
```

## 3. Hallucination control

Four layers. Each catches what the one before it misses.

1. **Prompt contract.** Facts only from tools; `not_found` must be said; no answering policy from general knowledge.
2. **Retrieval that admits ignorance.** A knowledge chunk must match at least half of the question's key words, so "history of the gold standard" no longer matches a payments page on the word "standard".
3. **Deterministic evidence guard** (`guardrails.evidence_check`). It blocks:
   - amounts, decline codes or transaction ids not present in this turn's tool data (this also catches facts carried over from an earlier transaction)
   - an affirmed outcome opposite to the record ("failed ... the payment was approved"), while negated contrast ("declined, not approved") is allowed
   - status claims with no lookup this turn, or about a transaction that wasn't found
   - refund, dispute or settlement claims with no matching field or document
   - URLs that didn't come from a knowledge-base result
4. **Bedrock contextual grounding** on the output, with tool results as `grounding_source`, for free-text explanations the deterministic checks can't parse.

**Repair, not just refusal.** A withheld answer is not a completed task. On an evidence failure the model gets one `<verifier>` message naming the unsupported claims and may re-check with tools. A second failure falls back to "I won't guess". `Turn.repaired` and the audit log record which path was taken. Set `repair_attempts=0` to turn this off.

### Stress test

`scripts/run_eval.py --hallucinate` injects one known hallucination into the model's first answer for every eval question, in 10 ways, and counts how many reach the user. Dev split (27 cases):

| Mode | Injected | Repaired | Fallback | Leaked |
|---|---:|---:|---:|---:|
| status_flip | 28 | 28 | 0 | 0 |
| invented_amount | 28 | 28 | 0 | 0 |
| invented_url | 28 | 28 | 0 | 0 |
| stale_txn | 28 | 28 | 0 | 0 |
| unlooked_status | 28 | 28 | 0 | 0 |
| bad_retry_advice | 5 | 5 | 0 | 0 |
| fake_cascade | 22 | 22 | 0 | 0 |
| bad_cascade_advice | 3 | 3 | 0 | 0 |
| fake_outage | 12 | 12 | 0 | 0 |
| false_promise | 28 | 28 | 0 | 0 |

That is 210 injected, 0 leaked. On the held-out split (`--data data/test`): 1,392 injected, 0 leaked.
`false_promise` adds "We guarantee the funds will be back in your account by Friday"; the guard
policy's commitment rules catch it and the repair removes it.

`bad_retry_advice` applies only to answers that contain a "don't retry" instruction. Separately, 448 correct answers (every failed transaction, asked as both merchant and support) produced **0 false blocks**.

Each leak the stress test found became a fix and a regression test:
1. "failed ... the payment was approved" passed a naive polarity check. Fixed with negation-aware matching.
2. "You can retry it right away" on a hard decline passed because "another card" appeared in a *different* sentence. Retry advice is now judged sentence by sentence.
3. "The payment was approved" on a processor **timeout** passed because only declined and approved records were checked. Unknown outcomes now block any definite claim either way; hedged wording is allowed.

**Limits.** These are 10 injection patterns on a scripted model, so the zeros show the guards catch these patterns, not that Claude never hallucinates. Run the same stress test with `FINBOT_LLM_PROVIDER=bedrock`: the corruption is then added on top of Claude's real answers, which tests the guards against realistic wording. Paraphrased errors with no number, code, URL or status word (for example a wrong *reason* for a decline) are left to contextual grounding and human review.

## 4. Payment retry guardrail and routing (`finbot/policy.py`)

The rules for when a failed payment may be retried, and who must act, are decided in **code**, not by the model. For every non-approved transaction, `get_transaction` attaches a `guidance` object:

| Field | Example (decline 05, merchant asking) |
|---|---|
| `retryable` | `false` |
| `retry_rule` | Do not retry with the same card details. |
| `next_step` | Ask the customer to use another card or call their bank. Don't retry. (worded for the asker's role: merchant or support) |
| `tell_the_cardholder` | Contact the card's issuing bank, or pay with another card. |
| `retry_only_if` | another card, re-enter, ... (the only acceptable forms of retry advice) |

It covers 10 issuer decline codes and 5 gateway errors, with an `UNKNOWN` default that is safe (don't retry). Each case is routed to the party who can fix it:
- **Cardholder's bank:** issuer declines.
- **Merchant's risk settings:** risk blocks. Thresholds are never disclosed.
- **Merchant's integration:** idempotency key reuse.
- **Status check before anything else:** processor timeouts.

The guard (`retry_advice_check`) blocks:
- retry advice for a non-retryable failure, unless the same sentence contains an allowed alternative ("retry with another card", "check the status, then retry with the same idempotency key")
- "retry now / immediately / keep retrying" when the rule requires waiting an hour
- any definite approved or failed claim when the outcome is unknown

"Retry" used as a noun ("Retry policy: ...") is not treated as advice.

**The rules are an example policy** matching the synthetic knowledge base. Replace them with your acquirer's and the card networks' current reattempt rules before production.

## 5. System retries and outages (`finbot/resilience.py`)

| Dependency | Retries | When it stays down |
|---|---|---|
| Claude (Bedrock or API) | One layer: full-jitter exponential backoff (1 s base, 16 s cap, 3 attempts) on 429, 5xx, timeouts and connection errors. SDK retries are set to 0 so they don't stack. Circuit breaker: opens after 5 failures, probes again after 30 s | The turn is **rolled back and not committed**. The user is told to try again, with a reference number. Resending the same message later gets a real answer, not a cached apology |
| Transaction DB, log search, KB | Backoff, 3 attempts; safe because the tools are read-only. Separate breaker per backend | The tool returns `error`, and the bot says "temporarily unavailable". An outage is never treated as "transaction not found" |
| Bedrock Guardrails | botocore adaptive retries (4 attempts, 10 s read timeout) | **Fail closed.** Neither unguarded input nor unguarded output gets through, and the model is not called |
| 400s, auth errors, `not_found` | Never retried | Handled as normal answers or errors |

**Escalating to a person:** after two unverifiable answers in a row (`escalate_after_fallbacks`), the reply directs the user to a human:
- a merchant gets a reference and the support contact (`FINBOT_SUPPORT_CONTACT`)
- a support agent is told to escalate to tier 2 with the session reference

A verified answer resets the count. The audit log records `escalated`.

## 6. Model orchestrator engine (`finbot/models.py`)

Every model call goes through one engine, which sits in front of an ordered chain of model targets. Each target has its own retry budget and circuit breaker.

```
turn ─► router (code) ─► fast chain (optional) ─► strong chain: Bedrock us-east-1 ─► Bedrock us-west-2 ─► Claude API
                                   each target: retries with backoff ─► breaker closed / open / half-open
```

| Feature | Behaviour |
|---|---|
| **Failover chain** | `FINBOT_MODEL_CHAIN="bedrock:anthropic.claude-opus-5-5@us-east-1,bedrock:anthropic.claude-opus-5-5@us-west-2,anthropic:claude-opus-5-5"`. A target that keeps failing (429, 5xx, timeouts) is skipped and the next one serves. Failover also works **mid-turn**: the next model continues the same append-only history |
| **Circuit breaker per model** | Closed: calls flow. **Open** after 3 consecutive failed calls: the target is skipped without waiting. **Half-open** after 30 s: exactly one probe request is let through. If the probe succeeds the breaker closes; if it fails, the cooldown doubles (capped at 5 min), so a flapping endpoint is probed less often |
| **Refusal fallback** | A `refusal` stop reason is retried on the next target. Bedrock has no server-side fallback, so the engine does it |
| **Complexity routing** (opt-in) | `FINBOT_ROUTING=tiered` with `FINBOT_FAST_MODEL="bedrock:anthropic.claude-sonnet-5-5@us-east-1"`. Code decides the tier: simple single-transaction questions go to the fast tier. Disputes, comparisons, several transactions, long questions, any turn after a fallback and **every repair attempt** go to the strong tier. If the fast tier is down, the turn falls back to the strong chain. Off by default, because choosing a cheaper model is a business decision |
| **No failover on bugs** | A 400 validation error or an auth failure is raised, not failed over: it is a config problem that needs fixing |
| **Health** | `engine.status()` and `/status` in the CLI show, per target: breaker state, consecutive failures, cooldown, calls, ok, failures, refusals, `served_as_failover`, p50 latency. Each turn records `models_used` and `tier` in the audit log |

**Trade-offs to know:**
- Prompt caches are per model, so a failover or a tier change starts with a cold cache. Expect higher latency and cost until the primary recovers.
- Thinking blocks from one model are passed back unchanged; the API drops, unbilled, any block the next model can't read.
- SDK retries are set to 0 so that retries happen in one place, with one backoff policy.

**Bug found while testing:** the engine checked the breaker with `allow()` and the retry layer called `allow()` again. In half-open state the first call took the single probe slot, so the recovered primary was never retried and traffic stayed on the backup. `available()` is now a read-only check for routing, and only the actual call takes the probe slot. A regression test covers it.

## 7. How the layers compose: guardrail boundary, crash recovery, resilience

The model engine is **inside** the guardrail boundary and **inside** the crash-safe turn. Neither the guards nor the store know which model answered, and the engine knows nothing about guards or sessions. That separation is what lets them combine safely.

```
Worker (runtime.py) ── claim lease ── load committed state ── journal turn (msg_id)
 │
 ├─ Input guardrail ............................ fails CLOSED if unreachable; blocked input never reaches any model
 │
 ├─ for each model call in the turn:
 │     heartbeat (renew lease; stop if another worker took over)
 │     engine.create(deadline=turn start + 90 s)
 │        └─ chain: [fast?] → primary → secondary → ...   breaker per model, shared via the store
 │              retries with backoff, never past the deadline; refusal → next model
 │     tools (read-only, own retries and breakers)
 │
 ├─ Evidence guard → one repair (strong tier) → fallback "won't guess" → human after 2 in a row
 ├─ Output guardrail (contextual grounding) ..... fails CLOSED
 │
 └─ renew lease ── commit (compare-and-swap on version + lease) ── reply
```

| Situation | What happens | Test |
|---|---|---|
| Input is an injection | Blocked before the engine: **0 model calls** on any target | `test_input_guard_runs_before_any_model` |
| Primary down, backup answers with a made-up fact | The backup's output passes through the same evidence guard; caught and repaired | `test_failover_model_output_is_still_verified` |
| Primary refuses, backup is "more willing" | Refusal fallback can't get around the guards: the backup's answer is checked like any other | `test_refusal_fallback_cannot_bypass_guards` |
| Guardrail service down, models healthy | Fail closed; the engine is never called | `test_guardrail_outage_fails_closed_...` |
| Worker crashes while the backup model is answering | A crash is **not** a model failure, so there is no failover inside the dead process. Nothing commits. The replacement reads the shared breaker state, skips the dead primary **without trying it**, and re-runs the turn on the backup | `test_worker_crash_during_failover_...` |
| One worker finds a model down | Its breaker publishes "open" to the store, and every other worker skips that model. When any worker's half-open probe succeeds, it publishes "closed" and all workers return to the primary | `test_breaker_state_is_shared_across_workers` |
| Long but healthy turn (2 × 40 s calls, 60 s lease) | The heartbeat renews the lease before every model call and again before commit, so the answer is saved. **This used to fail at commit with "lease lost"** | `test_long_healthy_turn_keeps_its_lease` |
| Worker stalls in one call longer than its lease, and another worker takes over | At its next heartbeat the stalled worker sees the new owner and stops; it never commits. The compare-and-swap commit is the final backstop | `test_heartbeat_stops_a_superseded_worker_early` |
| Every model failing slowly | Retries, backoff and failover all stop at the 90 s turn deadline. The user gets "try again" with a reference, the turn stays uncommitted, and resending the same message is safe | `test_retries_and_failover_stop_at_the_turn_deadline` |

### Rules that keep it safe

1. **Order of trust:** input guard → model → evidence guard → output guard. The engine can change *which* model runs, never *whether* the guards run.
2. **Only committed state counts.** Mid-turn failover, repair and retries all happen in memory. A crash at any point loses only the uncommitted turn, which is re-run from the last commit (tools are read-only).
3. **Model failures and process failures are handled differently:**
   - a 503, 429 or timeout is a **model** failure: retry, then fail over
   - an exception that kills the process is a **worker** failure: lease expiry, then takeover
   - a 400 or an auth error is a **bug**: raised, not hidden by failover
4. **Time budgets nest:** each retry delay stays inside the turn deadline (90 s), and the lease is renewed while work continues. A worker stuck inside a single long call loses ownership after its lease (60 s) lapses, and if it comes back its commit is rejected. Users wait at most the deadline (plus at most one in-flight call); a dead worker is detected within one lease.
5. **Health is shared, decisions are local:** breaker open/closed state goes through the store (wall clock). The probe slot and per-call retries stay in each worker. With N workers, up to N probes can hit a recovering model at once, at most one per worker.

### Production mapping

| Here | Production |
|---|---|
| SQLite `model_health` table, read on each breaker check | DynamoDB or Redis key with a TTL, cached for 1–2 s per worker to avoid a read per call |
| Lease heartbeat before each model call | Same, plus a timer-based heartbeat for single calls that can exceed the lease (long streaming outputs) |
| `turn_deadline_s = 90` | Tune it below the client or API-gateway timeout so the user gets "try again", not a gateway 504 |

## 8. Research link (MemoryKeeper)

Restarting an agent is established engineering. What the paper can contribute is whether **correction and authorization state** survive the restart. The same harness supports that experiment: give every memory baseline identical store, lease and journal infrastructure, then inject crashes:

- after a correction is received but before it is validated
- after a correction is committed but before the summary is refreshed
- during a handoff with unresolved concerns still open

Measure whether the replacement keeps valid corrections, rejects unauthorized ones and keeps open concerns. The existing pilots have not yet demonstrated crash-safe takeover.
