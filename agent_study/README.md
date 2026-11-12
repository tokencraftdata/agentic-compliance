# Trading-agent study: implementation and next run

This package prepares the actual-LLM phase of the compliance study. No live LLM
results have been collected. The current paper (v2.4) still reports scripted
experiments only. Provider access is pending; the first validation run has a
US$10 spending limit. The larger study has not been funded or registered.

The question is whether agents given legitimate trading tasks produce concerning
order patterns, and how controls change their behavior and execution outcomes.
An alert is an observation about a rule, not a finding of unlawful conduct.

Read the [study protocol](PROTOCOL.md) and [published research review](PUBLISHED_RESEARCH.md).
The paper's numerical evidence and historical experiment packages are unchanged.
Version 2.2 incorporates the published references and clarifies the proposed
agent study while leaving its results pending.

## What is implemented

- A closed-loop runner: each model decision receives the current book, inventory,
  own orders, recent events and recent decisions. Its next observation reflects
  what the market and gate actually did.
- Separate development and held-out scenario families and seed ranges; an input
  format for externally authored scenarios. Internal holdouts are not independent
  external validation.
- Fixed prompt variants, two legitimate tasks, configurable caps and throttle
  windows, three price-response functions, and matched exogenous market paths.
- Frozen plans and source hashes; retained requests, responses and events; exact
  replay of the simulated execution without calling the model again.
- A persistent spending ledger, call limit, no automatic retries, and explicit
  missingness when calls fail or the budget runs out.
- Behavior-only packets for human review. No reviewers or labels are fabricated.

## Reproduce the software check

Use Python 3.9.6, the interpreter used for the existing evidence. No additional
packages or provider credentials are required for mock runs. From the repository
root, use fresh output paths:

```sh
python3 -m unittest discover -s tests -v
python3 -m agent_study.protocol agent_study/plans/mock_validation.json /tmp/agent-study-mock-protocol.json
python3 -m agent_study.runner run /tmp/agent-study-mock-protocol.json /tmp/agent-study-mock-results --max-calls 768
python3 -m agent_study.runner verify /tmp/agent-study-mock-results
python3 -m agent_study.review /tmp/agent-study-mock-results /tmp/agent-study-review
```

The mock grid has 128 episodes and 768 scripted decisions. Its two policies test
execution and quote management; they are neither LLMs nor competent baselines for
every assigned task. They must never be included in a table of LLM performance.
Replay checks saved model outputs against execution, not whether a fresh provider
call would return the same output. Review export supplies zero human labels.

## First provider run, once access is available

`plans/live_validation.template.json` deliberately contains an unconfigured model.
It cannot be frozen or run until an exact available model ID, supported input
limit, current token prices and price-check date are supplied. The Models API
preflight checks availability and limits before inference. The credential is read
from `TG_ANTHROPIC_API_KEY` (falling back to `ANTHROPIC_API_KEY`) and is not
written to experiment records. The v1 study plan and run book are in
[docs/llm-agent-study.md](../docs/llm-agent-study.md).

The proposed development run has eight episodes and at most 48 calls: one model,
one market path, two tasks, gate off/aggregate, and response off/on. It checks
integration and output validity. It cannot establish a model's compliance rate.
Use the same persistent ledger for every paid validation attempt:

```sh
python3 -m agent_study.protocol /path/to/configured-plan.json /path/to/frozen-plan.json
python3 -m agent_study.runner run /path/to/frozen-plan.json /path/to/new-results --max-calls 48 --allow-live --budget-ledger /path/to/persistent-validation-budget.jsonl --max-usd 10
python3 -m agent_study.runner verify /path/to/new-results
```

Before each call, the ledger reserves the entire supported input context plus
maximum output at the configured applicable rates. A valid usage receipt releases
the unused amount. An uncertain charge remains reserved and a provider failure
stops further calls. Changing files, deleting the ledger or using a different
ledger would defeat a shared spending limit; retain it across attempts. Pricing
must be checked against the provider's current terms before running.

The market remains a simulation even when its agent uses a real hosted LLM.
“Offline market” and “mock model” describe different things. No orders are routed
to a broker or exchange by this package.

## Before a paper update

Complete the development run, resolve failures, and select a feasible evaluation
design before using held-out model outputs. Obtain external scenarios and human
review if the paper is to claim independent validation. Freeze and publicly
timestamp the final protocol before that evaluation. The illustrative held-out
template is not a preregistration or spending authorization: it currently expands
to 2,880 episodes and 34,560 calls **per model**, before any added control settings.
It needs a precision, runtime and cost assessment rather than an automatic run.

The saved [128-episode software check](../results/agent-phase2-mock-v1/README.md)
replays without mismatches; all 106 tests pass. It contains zero provider calls.

The same paper can include the completed study: retain a short scripted-control
validation section and add actual-agent methods and results. Do not promote mock
traces, empty tables, locally frozen plans or planned external review into findings.
