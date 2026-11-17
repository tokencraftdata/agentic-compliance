# Architecture decisions

*November 2026.* The decisions that shape finbot, one short record each: what was decided, why,
and what it costs. A new decision is a new record; a reversed one is marked "superseded", not
deleted. Where to read further is in [README.md](README.md).

---

### ADR-001 The model drafts; code decides what may be said
**Status:** accepted. **Context:** a support answer about money must not state what the records
do not support, and prompts cannot guarantee that. **Decision:** every draft passes a
deterministic verifier chain against this turn's tool results (`verification.py`); one guided
repair, then an answer built by code from the record, then "I won't guess".
**Consequences:** strictness costs refusals (cut from 39.9% to 8.9% by the record-built answer)
and pattern checks can miss new wordings; facts never rest on the model.

### ADR-002 The agent is read-only; changes become tickets
**Status:** accepted. **Context:** an injected or confused agent with write tools can move money.
**Decision:** every tool reads; "refund", "cancel", "unblock" open a ticket a person executes
(`actions.py`, `tickets.py`); the agent's grant ceiling is `read:*`.
**Consequences:** slower resolution for write requests; the blast radius of a successful
injection is a wrong sentence, which the gate catches.

### ADR-003 Each product area is a declared plug-in
**Status:** accepted. **Context:** one engine must serve accounts, transfers, KYC, lending,
cards, disputes and more without a code path per area. **Decision:** a frozen `Domain`
declaration (fields, statuses, policy table, cause phrases, summary) that the core checks
uniformly (`domains/`). **Consequences:** a new area is one module and tests; cause phrases are
global, so a loose phrase in one domain shows up as a false block in another.

### ADR-004 Provenance for arguments and values
**Status:** accepted. **Context:** injection screening misses text that holds no instruction.
**Decision:** a record lookup needs an id the user typed (argument provenance); a fact in the
answer must come from a field our own systems set, not merchant or processor text (value
provenance, `provenance.py`). After CaMeL, at the scale a read-only bot needs.
**Consequences:** the model cannot follow a record to another record without asking the user.

### ADR-005 Stateless workers with leases and compare-and-swap commits
**Status:** accepted. **Context:** pods crash, freeze and get rescheduled mid-turn.
**Decision:** a worker leases the session, journals the turn by `msg_id`, renews the lease on a
heartbeat and commits with a version check; a crash commits nothing and the next worker re-runs
from the last commit (`runtime.py`, `store*.py`). No workflow engine for one-step turns.
**Consequences:** exactly-once commits, verified with real kills (`scripts/durability_test.py`);
side effects need their own intent log (`begin_action`), which a workflow engine would provide.

### ADR-006 The API enqueues and waits; workers run turns
**Status:** accepted (supersedes "API runs turns in-process" for production).
**Context:** an API pod that runs turns needs model permissions and drops a turn if it dies.
**Decision:** in queue mode the API enqueues on SQS FIFO (group = session, dedup = msg_id), polls
the store for the commit up to a bound, and returns 202 for the client to resend the same
`msg_id` (`dispatch.py`). Inline mode stays for dev and single-box use.
**Consequences:** API pods need only queue send and session reads; clients must handle 202.

### ADR-007 Encryption per value, bound to tenant, session and purpose
**Status:** accepted. **Context:** a database dump or a misplaced row must not expose or move
data. **Decision:** AES-256-GCM with a KMS-wrapped data key per value; the encryption context
names tenant, session and purpose (`crypto.py`, `fbenc2:`; `fbenc1:` still opens).
**Consequences:** one KMS call per value written or read; per-tenant keys are a later step.

### ADR-008 Tenant isolation enforced in IAM, not only in code
**Status:** accepted. **Context:** an application bug must not be able to read another tenant.
**Decision:** session ids start with the tenant; session items are reached only with credentials
from a role tagged with the request's tenant, limited by `dynamodb:LeadingKeys` (`tenancy.py`,
`infra/cdk`). The tag comes from the verified caller, never from the key being read.
**Consequences:** an STS call per tenant per hour per pod; legacy session ids need new sessions.

### ADR-009 Guardrails adapt without retraining, through a gated rollout
**Status:** accepted. **Context:** new attack and failure patterns appear after launch.
**Decision:** rules are learned from reviewer-labelled mistakes, must pass the evaluation,
held-out and red-team suites, then run in shadow, then canary with an admin's approval;
versioned with one-call rollback (`adaptive/`). Model weights never change.
**Consequences:** a person stays in the loop for every rule; upkeep is reported per rule.

### ADR-010 Never cache records or answers
**Status:** accepted. **Context:** a cached "approved" after a reversal is the wrong answer the
gate exists to stop. **Decision:** cache only help-page searches (minutes, invalidated by
re-ingest) and model health (one second) (`cache.py`); no semantic answer cache.
**Consequences:** every turn reads the systems of record; latency and load follow from that.

### ADR-011 Observability without content
**Status:** accepted. **Context:** traces and security events go to third-party tools.
**Decision:** OpenTelemetry GenAI spans and OCSF-style security events carry decisions, reason
codes and counts; attributes are an allow-list; no prompts, answers, record ids or raw session
ids (`telemetry.py`, `security_gateway.py`).
**Consequences:** debugging a single answer needs the encrypted session store, by design.

### ADR-012 Reconcile where two stores can drift
**Status:** accepted. **Context:** tickets, audit lines and the help-page index live apart from
the session store. **Decision:** read-only jobs report drift: tickets vs records, audit vs
commits, index vs source; a weekly full sync; unknown actions settled before the next turn
(`reconcile.py`). **Consequences:** a person acts on findings; nothing is auto-corrected.

### ADR-013 Releases are gated by behaviour, not only by tests
**Status:** accepted. **Context:** a change can pass unit tests and still let a hallucination or
an injection through. **Decision:** a release candidate must pass the evaluation sets, stress
test, false-block probe, consistency, red team and durability scenarios with fixed thresholds
(`scripts/release_check.py`), then go out staging → canary → full with automatic rollback
signals ([release.md](release.md)). **Consequences:** a release takes minutes of checks; a
threshold miss blocks it.
