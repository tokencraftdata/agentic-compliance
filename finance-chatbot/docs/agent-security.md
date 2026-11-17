# Agent security: a guardrails platform, and twelve questions

*November 2026.* How finbot evaluates AI requests and tool calls before they run, and how the
same design answers the usual questions about securing AI agents. Each answer points at code;
"platform" marks what a multi-team deployment would add (see [platform-design.md](platform-design.md)).

## 1. The guardrails platform

```
 request ─► edge: WAF, token, rate limit, DLP in ─► input guard (injection, denied topics, learned rules)
                                                        │
                                       model drafts ◄───┘
                                            │ tool call
                                            ▼
            ┌───────────────────── security gateway (security_gateway.py) ─────────────────────┐
            │ 1 argument provenance: record ids must come from the user        → refuse         │
            │ 2 schema: tool exists, arguments fit its JSON schema              → deny           │
            │ 3 deployment rules (tool, role, effect)                           → deny | approve │
            │ 4 bulk access: more than 3 records in one turn                    → approve        │
            │ 5 delegation grant: scope for this tool, tenant, session, expiry  → refuse         │
            └────────────────────────────────────────────────────────────────────────────────────┘
                                            │ allowed
                                            ▼
                       tool runs (read-only) ─► result screened (injection, PII) ─► spotlighted
                                            │
            behaviour monitor: refusals, scope denials, invalid arguments, injected text,
            enumeration, held calls → session risk → lock at the threshold
                                            │
 answer ◄─ DLP out ◄─ output guard ◄─ hallucination gate (facts, value provenance, contacts, links)
                                            │
                     security events (one stream) ─► audit log (hash-chained, Object Lock)
```

| Requirement | finbot | Code |
|---|---|---|
| Prompt injection detection | user text normalised and screened, Bedrock prompt-attack filter on top; tool results screened and spotlighted; provenance on arguments and values; prompt-leak canary; multi-turn risk score | `untrusted.py`, `guardrails.py`, `provenance.py`, `adaptive/risk.py` |
| Sensitive data / PII | masked before storage, logs, queues and the model; DLP at the edge both ways; `egress_leak` alert | `pii.py`, `dlp.py`, `service.py` |
| Tool invocation validation | the gateway checks schema and rules before every call; grants check scope; provenance checks where ids came from | `security_gateway.py`, `agent_identity.py`, `orchestrator._provenance` |
| Policy enforcement | deployment rules (deny or approve by tool and role, from `FINBOT_TOOL_POLICY`); retry and routing policy tables; learned commitment rules with a gated rollout | `security_gateway.ToolRule`, `policy.py`, `adaptive/` |
| Human approval | a held call opens an approval ticket keyed by session, tool and arguments; once a person resolves it "done", exactly that call runs; write requests always become tickets | `SecurityGateway._approval`, `tickets.TicketDesk` |
| Suspicious agent behaviour | behaviour monitor adds risk for provenance refusals, scope denials, invalid arguments, injected text, enumeration and held calls; the session locks and goes to a person | `BehaviorMonitor`, risk lock stage |
| Security event logging | one event per decision, OCSF-style (class, severity, actor user and agent, tenant, hashed session, resource, decision, reason codes); no values; written to the hash-chained audit trail | `SecurityEvents`, `auditlog.py` |

Event classes: `prompt_injection`, `prompt_leak`, `unsafe_output`, `tool_call_denied`,
`tool_call_held`, `tool_call_approved`, `suspicious_agent_behavior`, `sensitive_data`,
`egress_leak`.

**Platform:** run the gateway as a shared service that every team's agents call (policy decision
point), with rules per team in a policy store, decisions cached for a few seconds, and events
streamed to the SIEM. The interface stays `check(agent, tool, args) → allow | deny | approve`.

## 2. Twelve questions

**Identity: how does every agent get a unique identity?**
finbot: each turn, code mints a delegation grant with `sub` = the user and `act.sub` = the agent,
plus tenant, session, scopes and a five-minute expiry (`agent_identity.py`, RFC 8693 shape), so
every action names both who asked and which agent acted. Platform: register each agent as a
workload identity (SPIFFE ID or an IAM role per agent, e.g. `spiffe://corp/agents/<team>/<agent>`),
issued at deploy time, and put that ID in `act.sub`.

**Authentication: how do agents authenticate to internal services?**
finbot: the model holds no credential; code presents the grant, and a remote tool service can
verify it itself (`GrantIssuer.verify`). Pods reach AWS with IAM roles, no static keys. Platform:
mTLS with short-lived workload certificates between services, plus the grant as a bearer token
for the user context; sign grants with an asymmetric KMS key so services verify with a public key.

**Authorization: how is least-privilege tool access enforced?**
Four checks, each deny by default: the grant's scope must cover the tool (`<domain>:read`), the
agent's ceiling is read-only whatever the user holds, the gateway's rules can deny or require
approval by tool and role, and argument provenance stops the model choosing records the user
never named. Below the application, IAM confines session data to one tenant
(`dynamodb:LeadingKeys`).

**Credentials: how are agent credentials distributed and rotated?**
finbot: grants live five minutes and are minted per turn, so there is nothing to rotate; signing
keys and token keys are rings addressed by `kid` (old keys verify until removed); KMS rotates the
data key yearly. Tenant data credentials come from STS with a one-hour ceiling and refresh a
minute before expiry (`tenancy.AssumedRoleTables`). Platform: a credential broker injects
per-tenant secrets into outbound calls after the grant check; secrets rotate in the secrets
manager without the agent seeing them.

**Revocation: how do you cut off a compromised agent at once?**
finbot: revoke one token by `jti` or every token of a subject issued before now; the deny list
lives in the shared store and every pod sees it within two seconds. Grants expire in five
minutes anyway. A model can be drained from the control plane, and a session locks itself when
the behaviour monitor crosses the threshold. Platform: add a per-agent kill switch in the policy
store (the gateway denies everything for that `act.sub`) and revoke its workload certificate.

**Prompt injection: how do you stop an agent following instructions in retrieved content?**
Assume screening will miss some, and limit what a successful injection can do: tool results are
screened and wrapped in a nonce delimiter; the agent is read-only; record ids must come from the
user (argument provenance); facts in the answer must come from fields our own systems set, not
from merchant or processor text (value provenance); links must be a help page's own URL; asking
for secrets is blocked. Red team with a model that obeys anything: 0 of 34 attacks succeed with
defences on, 0 of 34 with screening off, 25 of 34 with nothing.

**Data security: how do you keep sensitive data from reaching the LLM provider?**
Field allow-lists drop hidden fields (review reasons, credit and fraud scores) before the model
sees a record; card data, SSNs and other identifiers are masked on the way in; Bedrock runs in
the account's region behind a VPC endpoint, and Bedrock does not use prompts for training;
telemetry and security events hold no content.

**Audit: how do you keep an immutable trail of every agent action?**
One JSON record per turn and per security decision, each hash-chained to the previous with an
HMAC key (`auditlog.py`; editing, deleting or reordering breaks verification,
`scripts/verify_audit.py`), shipped to an S3 bucket with Object Lock in compliance mode for a
year, under its own KMS key so session readers cannot read or erase it.

**Reliability: how do you handle an LLM provider outage?**
A failover chain (Bedrock in two regions, then the Claude API) with a circuit breaker per model
shared by all workers, refusal fallback, a 90-second turn deadline, and an honest "try again"
with the turn left uncommitted so a resend gets a real answer. Guardrail outages fail closed.

**Scalability: how do you support thousands of concurrent agents?**
Stateless workers, one turn in flight each, scaled on queue depth; sessions are rows that cost
nothing idle. finbot's own code costs about 2.4 ms CPU and 45 MB per worker; the model sets the
pace, so the ceiling is Bedrock quota (`scripts/load_test.py`, technical-design.md 10.3). Platform:
concurrent calls per pod, per-tenant quotas and fair scheduling.

**Observability: how do you trace an agent across tools and services?**
OpenTelemetry spans with GenAI conventions: `finbot.turn` → `chat <model>` → `execute_tool <tool>`
→ `finbot.verify`, one trace id per turn; spans carry decisions and reason codes, never content
(`telemetry.py`). finbot's tools run in-process; with remote tool services, pass the trace
context in the standard `traceparent` header so their spans join the same trace. Security events
carry the same tenant and hashed session for correlation.

**Governance: how do you roll out new agent capabilities safely across teams?**
A capability is a domain plug-in with its own field allow-list, scope and tests. Changes to guard
rules follow a gate: evaluation, held-out and red-team suites must pass, then shadow traffic,
then a canary with an admin's approval, with one-call rollback (`adaptive/controller.py`).
Platform: a capability registry with owners, scopes requested per agent and approved by security,
rules per team in the gateway, and the same gate for prompts and model changes.

## 3. What is not built

- The gateway runs inside the bot, not as a shared service.
- Approval is ticket-based; there is no notification or approval UI.
- Agents do not have workload identities of their own; the grant names one agent, `finbot-agent`.
- Grants are signed with HS256; asymmetric signing is the production step.
- Nothing has run against a live model or on AWS.
