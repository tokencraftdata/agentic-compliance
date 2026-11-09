# Commercial assessment and customer-validation plan

Research date: 7 November 2026. Public-source desk research only. No customer interviews, outreach, sales, letters of intent or willingness-to-pay observations have occurred.

## Recommendation

Test a narrow product: **predeployment behavior testing and replayable evidence for teams building trading agents**. Start as a scoped evaluation service with a reusable test harness. A broad replacement for trade surveillance is a weak starting position: established suppliers already provide detection, investigation, risk monitoring and algorithm testing.

The present software is a research prototype. Its synthetic market is not exchange-calibrated, its control improvements target known scripts, and no LLM cohort has run. Those limitations prevent a credible production compliance or certification claim.

## Evidence ledger

| Public evidence | What it supports | What it does not establish |
|---|---|---|
| FINRA's 2026 oversight report discusses agent autonomy, scope, auditability, action tracking and behavioral guardrails (pp. 26-27). | A concrete supervisory concern that evaluation and evidence tools could address. | A requirement to buy this product or an identified buyer budget. |
| Alpaca offers agent-facing Trading and Broker API tools, including order placement and paper trading. | Trading-agent tool access is an observable product category. | The number of autonomous trading deployments, unsafe behavior rates, or interest in our tool. |
| Nasdaq reported 9 Surveillance client additions and 51 upsells in Q3 2026. | Existing demand for the broad surveillance category. | Demand specifically for predeployment LLM-agent testing; these are vendor-reported figures. |
| Eventus announced STX's deployment of Validus in August 2026. | A named organization procuring surveillance infrastructure. | An accessible lead, willingness to switch, purchase value, or confirmed interest in our offering. |
| Eventus markets algorithm monitoring and testing of new algorithms and updates. | Substantial competitive overlap, including testing. | That incumbents lack LLM-specific features; that must be established directly. |

Sources: [FINRA 2026 report](https://www.finra.org/sites/default/files/2025-12/2026-annual-regulatory-oversight-report.pdf), [Alpaca Trading MCP](https://alpaca.markets/mcp-server), [Alpaca Broker MCP](https://docs.alpaca.markets/us/docs/broker-api-mcp), [Nasdaq Q3 2026 results](https://ir.nasdaq.com/news-releases/news-release-details/nasdaq-reports-third-quarter-2026-results-strong-execution), [Eventus/STX announcement](https://www.eventus.com/cat-press-and-news/stx-deploys-eventus-validus-platform-for-trade-surveillance/), [Eventus algo monitoring](https://www.eventus.com/algo-monitoring/).

The inference from these sources is that the problem deserves buyer discovery. They do not validate demand for this particular product. No top-down market-size claim is made. AML agent adoption and total regulatory-technology revenue should not be repurposed as this product's addressable market.

## Buyer hypotheses, ordered for discovery

| Segment | Likely user / economic sponsor | Trigger to investigate | Principal obstacle |
|---|---|---|---|
| Agent-enabled brokerage/API product team | AI or trading engineering lead / product and risk leadership | A new order-entry agent or model update awaiting approval | Integration, private traces, and unclear division of engineering versus compliance budgets |
| Quant or electronic-trading team evaluating LLM workflows | Research engineer / trading technology or model-risk leader | Moving an assistant from advice into order proposal or execution | Whether a stylized simulator transfers to their strategies and venues |
| Surveillance or governance vendor | Product manager / business-unit sponsor | Customer asks for agent action lineage and regression scenarios | Existing capability, partner economics and easy internal replication |
| Exchange surveillance function | Surveillance analyst / compliance or technology sponsor | New participant behavior or market launch | Mature incumbents, procurement requirements, venue-specific data and long validation cycles |

First discovery target: an engineering team that already has an agent and an actual approval bottleneck. Large banks and exchanges are useful expert reviewers, but should not be assumed to be the fastest first customers. This prioritization is a hypothesis, not a measured sales-cycle estimate.

## Alternatives and differentiation to test

| Alternative | Existing strength | Proposed reason to choose or supplement it |
|---|---|---|
| Nasdaq Surveillance | Market-abuse detection and investigation workflows | Export reproducible predeployment agent scenarios into an existing review process |
| Eventus Validus | Surveillance, risk controls, algorithm monitoring and testing | Show incremental value from prompt/model version changes and attempted-versus-executed action lineage |
| Internal simulation and replay | Domain fit, privacy and control | Reduce setup time and produce a consistent evidence package across models |
| General agent evaluation tools | Broad model/tool-call testing | Add market-specific execution semantics and trading-utility tradeoffs |
| Do nothing / manual review | No new integration | Demonstrate a recurring approval delay or missed failure with economic consequences |

Nasdaq product scope: [official product page](https://www.nasdaq.com/products/fintech/surveillance). Eventus scope: [algorithm monitoring](https://www.eventus.com/algo-monitoring/) and [risk controls](https://www.eventus.com/market-risk/). The proposed differentiation is unverified. Do not market “first,” “complete compliance,” “zero manipulation,” or a validated false-positive rate.

## Concrete design-partner offer

**Offer:** a four-week, simulator-only evaluation of one agent workflow and two model or control configurations, using synthetic or customer-authorized redacted data. Deliver a replayable scenario pack, attempted/executed action ledger, failure and inactivity accounting, utility tradeoff table, and an evidence report the customer's reviewer can reproduce.

**Acceptance criteria to agree before starting:** ingest the agreed sample successfully; reproduce every accepted test trace; demonstrate whether failures are new relative to the customer's current checks; and measure reviewer effort and useful trading retained. A candidate target is a 30% reduction in review effort on the agreed tasks, but this is a proposed success threshold, not an achieved benefit. If no incremental issue is found, report that outcome.

**Pricing experiment:** ask for a fixed-fee pilot with a written scope, named budget owner and purchase decision date. Test a USD 5,000 starting quote against the actual delivery effort and buyer response; it is a proposed offer, not a market benchmark or established willingness to pay. Do not build subscription forecasts from it. A paid commitment or completed procurement step is stronger evidence than positive interview feedback.

## Discovery process and decision rules

1. Interview 12 relevant people across at least 6 organizations: engineering, risk/compliance and product/procurement roles. Track organizations separately so multiple contacts are not counted as independent customers.
2. Ask about the last actual agent/model release: who approved it, what evidence they needed, which failures delayed it, how long review took, and what tooling they already used. Request a redacted example where authorized.
3. Show the split-order failure and the repaired control's trading costs only after understanding their workflow. Ask whether the example maps to a real decision they own.
4. Seek 3 organizations willing to supply an authorized workflow or trace sample and assign a reviewer. Seek 2 scoped paid-pilot commitments before substantial productization.
5. Stop or reposition if fewer than 3 organizations identify a recurring approval problem, if existing tools solve it adequately, or if nobody can name a budget owner. These are proposed decision rules, not statistical estimates of market demand.

Interview prompts: “Walk through your last approval”; “What did you have to rerun after a model change?”; “Which evidence was missing?”; “What would make this fail procurement?”; “What are you already paying for or building?”; “Who could authorize a scoped evaluation?” Avoid asking only whether the idea sounds useful.

## Ready-to-use tracking fields

Organization; segment; interview role; date; actual workflow; release frequency; approval owner; present tooling; concrete pain example; current effort; authorized sample available; security/data constraints; budget owner; pilot scope; proposed fee; procurement milestone; next decision date; disconfirming evidence; status.

No contacts have been messaged. Customer conversations, data sharing and paid commitments remain external validation steps. This document is a plan and evidence ledger, not a completed demand study.
