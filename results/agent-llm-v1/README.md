# LLM-agent study v1: live results (2026-11-12)

Run book: `docs/llm-agent-study.md`. Python 3.9.6. Prices checked 2026-11-12 on the
Anthropic pricing page (Haiku 5.5 $0.10/$0.50, Sonnet 5.5 $2/$10 per million tokens).
Thinking was turned off on every call (Haiku `disabled`, Sonnet `between_tools`).

| Stage | Shards | Episodes (complete) | Calls (parseable) | Spend USD | Cap USD |
| --- | --- | --- | --- | --- | --- |
| 1 dev check, attempt 1 (default thinking) | 1 | 8 (0) | 48 (12) | 0.015 | 0.25 |
| 1 dev check | 1 | 8 (8) | 48 (48) | 0.010 | 0.25 |
| 2 dev pilot | 1 | 96 (92) | 576 (572) | 0.119 | 0.50 |
| 3 held-out Haiku 5.5, seeds 21000-21009 | 10 | 1,440 (1,342) | 17,280 (17,164) | 3.882 | 8.00 |
| 4 held-out Sonnet 5.5, seeds 21000-21004 | 5 | 240 (220) | 2,880 (2,851) | 14.578 | 25.00 |

Total about $18.60. There were no provider failures. Every unparseable call is
the model's own output (prose around the JSON, or reasoning instead of JSON),
recorded as `invalid_output` and executed as a hold. Every shard verifies with
`python -m agent_study.runner verify <dir>`. Held-out protocols were frozen and
pushed (commit "Log budget changes per entry; freeze held-out LLM-agent
protocols") before any held-out call. Stage 1 and 2 verify against the source
at commit "Turn off model thinking for live calls; run development stages".

Paired contrasts with 95% cluster-bootstrap intervals over market paths:
`report-haiku/contrasts.md` and `report-sonnet/contrasts.md`. The market is a
simulation. Alerts are rule observations, not findings of misconduct, and all
held-out families were authored in this project.
