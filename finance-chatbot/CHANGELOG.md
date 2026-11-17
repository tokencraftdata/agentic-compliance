# Changelog

All notable changes to finbot. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [semantic versioning](https://semver.org/) as defined in
[docs/release.md](docs/release.md): a change to stored data formats, the HTTP API or a security
guarantee is called out under "Upgrade notes".

## [0.4.0] - Unreleased

Release candidate. Verified offline only (scripted model, synthetic data); not deployed.

### Added
- Twelve domain plug-ins (accounts, transfers, KYC, lending, cards, onboarding, disputes,
  mortgages, BNPL, insurance, subscriptions, card payments), selected with `FINBOT_DOMAINS`.
- Value provenance: facts in an answer must come from fields our own systems set.
- Security gateway: schema checks on every tool call, deny/approve rules (`FINBOT_TOOL_POLICY`),
  human approval for held calls, a behaviour monitor, and one security event stream.
- Multi-tenant isolation: tenant-prefixed session ids, tenant-bound encryption, per-tenant
  credentials enforced with IAM `LeadingKeys`, rate limits shared across pods.
- Queue dispatch (`FINBOT_DISPATCH=queue`): the API enqueues and waits; workers run turns.
- Turn cancellation (`POST /v1/sessions/{id}/cancel`).
- Cache for help-page searches and model health (memory or Valkey/Redis); never records.
- AI observability: OpenTelemetry GenAI spans without content.
- Reconciliation jobs: tickets vs systems of record, audit log vs committed turns, help-page
  index vs source; weekly full sync.
- Durability test with real processes (`scripts/durability_test.py`), load test, release gate
  (`scripts/release_check.py`), architecture decisions, requirements traceability, paper.

### Changed
- Session ids are `<tenant>.<uuid>`; new values are sealed as `fbenc2:` (tenant in the context).
- A session belongs to the user who started it; a change of role, groups or scopes requires a new
  session.
- Workers release the session lease when a turn ends normally.
- Turn journals are kept for a fixed 24-hour session lifetime.

### Fixed
- Twelve defects from an independent code review, each with a regression test (approval bypass
  on retry, session use by another user in the tenant, unreleased leases, two threads running one
  session, enumeration risk on repeats, and more; see docs/requirements.md).
- Leaked SQLite connections in tests.

### Upgrade notes
- **Drain old workers before starting new ones**; do not run old and new store writers together.
- Old DynamoDB sessions without a fixed expiry, and sessions with pre-tenant ids, need a new
  session. `fbenc1:` values still decrypt.
- With `FINBOT_DISPATCH=queue`, clients must handle `202 Accepted` and resend the same `msg_id`.
- With `FINBOT_TENANT_ROLE_ARN` set, deploy the `finbot-tenant-data` role from `infra/cdk` first.

## [0.3.0] and earlier

The guarded payments support bot: hallucination gate with one repair, record-built answers,
adaptive guardrail controller, processor orchestration and retry policy, crash-safe workers with
leases, model failover with circuit breakers, PCI masking on every path, write requests as
tickets, ingestion into a vector index, reference Kubernetes and CDK deployment.
