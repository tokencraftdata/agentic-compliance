# TradeGauntlet: Evaluating Compliance Controls for Autonomous Trading Agents

A replayable limit-order-book simulator for studying agentic compliance: how
autonomous trading agents interact with pre-trade gates and surveillance rules,
what the controls prevent, and what they cost in task performance.

**Paper:** [TradeGauntlet: Evaluating Compliance Controls for Autonomous Trading Agents](output/pdf/tradegauntlet_arxiv.pdf)
(source in [`paper/arxiv/`](paper/arxiv/README.md)).

All trading is simulated. Nothing here measures real-market detection accuracy or
establishes legal compliance.

## What it measures

TradeGauntlet evaluates LLM agents pursuing legitimate tasks, then uses scripted
probes and searching adversaries to test weaknesses beyond the behavior observed
in those runs. It separates three measurements:

- **Detection:** does a conduct rule or baseline detector alert?
- **Prevention:** does a gate remove the gain from moving the price, or only the alert?
- **Cost:** what do the controls cost legitimate traders in fills, P&L and false alerts?

The LLM study records 1,440 Haiku sessions and 240 Sonnet sessions, with 1,342 and
220 complete episodes, respectively. All conduct alerts were self-matches, which
the gates reject outright. The aggregate-gate condition reduced Sonnet's two-sided
fills by about 15%. A conduct reminder reduced Sonnet's alert rate but cost Haiku
fills without a detectable alert benefit.

Random search and a policy gradient find split-order schedules that evade the
implemented conduct rules. The learned baseline alone catches all 2,400 tested
searched episodes and flags 15/100 large-maker sessions. Combining detectors raises
that false-alert count to 23/100 without improving recall on this test set.

Aggregate caps reduce response-dependent gains unevenly. Their effect is smaller
when only near-touch depth affects prices. The paper labels ratios of the highest
held-out scores across search experiments as descriptive: those maxima were
selected after examining test results and do not use a common final test set.

The price response is fitted to five LOBSTER samples and its shape is checked in the
ABIDES agent-based market. A separate adversarial prompt elicited refusals from
Claude Sonnet 5.5 in all 240 rounds, yielding no candidate schedules to evaluate.

## Layout

| Path | Contents |
| --- | --- |
| `pilot/` | Simulator core: matching engine, scripted traders, gates and detectors |
| `paper_study/` | Runners for every experiment in the paper (each has `plan`, `run`, `verify`) |
| `results/` | Saved evidence: plans, configs, episodes, summaries, manifests |
| `paper/arxiv/` | Paper source (`main.tex`) and `figures.py`, which redraws figures from `results/` |
| `tests/` | Unit and integration tests |
| `agent_study/` | LLM-agent study harness (runner, scenarios, spending limits, replay); run book in `docs/llm-agent-study.md` |
| `experiments/` | Action format and Anthropic adapter used by `agent_study/` |
| `scripts/` | `agent_plans` and `agent_report`, which build and summarize the LLM-agent runs |
| `docs/` | Workflow guide and the LLM-agent run book |

Main result folders:

| Directory | Used for |
| --- | --- |
| `adversary-v1`, `adversary-budget-v1` | Adaptive adversary; repeat with 3x budget and 200 held-out seeds |
| `robustness-v1` | Response window, tick-unit mapping, search restarts, display gain |
| `empirical-paths-v1` | Stylized facts vs LOBSTER; adversary replayed on real price paths (LOBSTER data not included) |
| `real-path-search-v1` | Adversary search rerun on real price paths, three folds, each window scored out of sample (LOBSTER data not included) |
| `rl-adversary-v1` | Policy-gradient adversary over the schedule space: three runs at the random search's budget, one at 10x |
| `llm-adversary-v1` | Sonnet 5.5 asked to act as the adversary; it declined in all 240 rounds (1.69 USD) |
| `lobster-calibration-v1` | Fitted imbalance-to-price slopes (LOBSTER data not included) |
| `baselines-v1` | Order-to-trade ratio and learned detector |
| `execution-quality-v1` | Fill rate, shortfall, edge, markout and inventory of legitimate scripts |
| `abides-check-v1`, `abides-check-v2` | Display gain in ABIDES RMSC04 (v2 pairs arms on one fundamental path) |
| `abides-response-v1` | Whether the agent's own display moves the ABIDES midpoint (1, 2, 5 s; RMSC04 and a thin book) |
| `agent-llm-v1` | LLM-agent study: frozen protocols, replayable traces, paired contrasts (`report-*/contrasts.md`) |
| `sensitivity-v1`, `boundary-v1` | Step-response mechanism checks (paper appendix) |

## Reproduce

Everything except the ABIDES check uses only the Python standard library. Saved
evidence was produced with **CPython 3.9.6**, which the exact-match verify commands
require:

```sh
uv python install 3.9.6
PY="$(uv python find 3.9.6)"

$PY -m unittest discover -s tests                         # test suite
$PY -m paper_study.adversary verify results/adversary-v1  # rerun held-out evaluations
```

Every runner in `paper_study/` takes `plan`, `run` and `verify` and refuses to
overwrite existing output, so a fresh run goes in a new directory:

```sh
$PY -m paper_study.robustness plan results/my-run
$PY -m paper_study.robustness run results/my-run --workers 4
$PY -m paper_study.robustness verify results/my-run
```

The ABIDES check needs ABIDES (commit `f9cbe51`, Python 3.9) installed; see the
docstring of `paper_study/abides_check.py`. Build the paper with `latexmk -pdf main.tex`
in `paper/arxiv/`.

## Limitations

One synthetic instrument and account, no exchange latency, strategic background
traders or cross-venue routing. The price response is fitted on one day of data for
five stocks. Scripts, labels and detectors were developed together, and the analyses
are exploratory, not preregistered. An alert records a pattern, not unlawful conduct.

More: [workflow guide](docs/tradegauntlet.md), [LLM-agent run book](docs/llm-agent-study.md).

## Citation and license

> Sandip Nandi, Aishwarya Bose, Apurba Bose, and Soovadeep Bakshi. *TradeGauntlet: Evaluating Compliance Controls for Autonomous Trading Agents.* Preprint, 2026.

Code and data are released under the [MIT License](LICENSE).
