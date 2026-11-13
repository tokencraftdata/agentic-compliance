# TradeGauntlet: testing the limits of trading compliance controls

A small, fully reproducible limit-order-book simulator for stress-testing pre-trade
gates and surveillance rules against traders who adapt to them, and the paper that
reports what it finds.

**Paper:** [TradeGauntlet: Testing the Limits of Trading Compliance Controls](output/pdf/tradegauntlet_arxiv.pdf)
(source in [`paper/arxiv/`](paper/arxiv/README.md)).

All trading is simulated. Nothing here measures real-market detection accuracy or
establishes legal compliance.

## What it measures

Controls are compared on identical simulated market paths, scoring three things
separately: whether a rule alerts, whether a gate removes the gain from moving the
price, and how much legitimate trading the gate blocks. The paper uses this to show:

- a detector that shares a threshold with a gate cannot alert on gated traffic, so
  zero alerts there measure the gate, not the detector;
- a price response fitted to Nasdaq order-book data (five LOBSTER samples);
- a seeded search over split-order schedules that evades the implemented conduct
  rules, scored on held-out paths with bootstrap intervals;
- two baseline detectors (an order-to-trade ratio and a learned classifier);
- the execution-quality cost of each control to legitimate traders;
- stylized facts against the data, and the adversary replayed on real price paths;
- a check in the independent ABIDES market simulator;
- Claude Haiku 5.5 and Sonnet 5.5 trading legitimate tasks under the same controls.

## Layout

| Path | Contents |
| --- | --- |
| `pilot/` | Simulator core: matching engine, scripted traders, gates and detectors |
| `paper_study/` | Runners for every experiment in the paper (each has `plan`, `run`, `verify`) |
| `results/` | Saved evidence: plans, configs, episodes, summaries, manifests |
| `paper/arxiv/` | Paper source (`main.tex`) and `figures.py`, which redraws figures from `results/` |
| `tests/` | Unit and integration tests |
| `agent_study/` | LLM-agent study harness (runner, scenarios, spending limits, replay); run book in `docs/llm-agent-study.md` |
| `scripts/`, `experiments/`, `docs/`, `provenance/`, `paper/offline_study/` | Earlier pilot tools, history and the original v2.4 build |

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

More: [version history](docs/changelog.md), [workflow guide](docs/tradegauntlet.md),
[historical documents](docs/history/README.md).

## Citation and license

> Sandip Nandi, Aishwarya Bose, Apurba Bose, and Soovadeep Bakshi. *TradeGauntlet: Testing the Limits of Trading Compliance Controls.* Preprint, 2026.

Code and data are released under the [MIT License](LICENSE).
