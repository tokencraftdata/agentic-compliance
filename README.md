# TradeGauntlet: testing the limits of trading compliance controls

Trading platforms are starting to let AI agents place and cancel orders on a
customer's behalf. Before that happens at scale, someone has to ask whether the
usual safeguards still work: the pre-trade gates that reject risky orders and the
surveillance rules that flag spoofing or layering. This repository contains a small,
fully reproducible simulator built to stress-test those safeguards, the experiments
run with it, and the paper that reports them.

**Read the paper:** [TradeGauntlet: Testing the Limits of Trading Compliance Controls](output/pdf/order_splitting_and_timing.pdf)
(v2.4, 10 November 2026, exploratory technical report).

> **Status.** Every trade in the reported experiments comes from a fixed script or a
> seeded search over split-order schedules (`paper_study.adversary`).
> The LLM-agent study is built and passes its software checks, but no real model has
> been run yet. Nothing here measures real-market detection accuracy or establishes
> legal compliance.

## What we found

TradeGauntlet compares controls on identical simulated market paths and keeps three
things separate: whether a rule raises an alert, whether a gate removes the gain a
trader gets from moving the price, and how much legitimate trading the gate blocks.
Looking at all three exposes problems that alert counts alone hide.

1. **Splitting orders slips past a per-order limit.** A gate that rejects one 60-lot
   order accepts six 10-lot orders at the same price, with no conduct alert, in all
   40 tested seeds. An aggregate cap closes that gap, but whether it removes the gain
   depends on how strongly prices are assumed to react. With the same 20-lot cap, the
   mean gain is 0.00 tick-lots at response threshold 0.4 and 48.00 at threshold 0.2.
2. **Controls are expensive for honest traders.** The full policy cuts a large market
   maker's volume by 14.1% and a patient maker's by 28.2%, mostly because of the
   cancellation throttle. In a one-hour session, trading resumes after the lockout but
   volume stays 75.2% below the ungated baseline.
3. **Detectors can flag ordinary market making.** With a 180-second detector window, a
   legitimate maker triggers alerts in 39 of 40 episodes when it re-quotes every 170
   seconds, and in 0 of 40 when it re-quotes every 190 seconds.

## How it works

TradeGauntlet is a custom limit-order-book simulator, written for this project in
plain Python (it is not built on ABIDES and does not connect to any exchange).

- **Market.** One instrument, integer price ticks, price/time priority and partial
  fills. Background liquidity refreshes every second, and the fundamental price and
  noise orders are drawn before trading starts, so every control setting sees the
  same market for a given seed.
- **Price response.** In the "responsive" condition, background quotes shift by two
  ticks when the order book becomes lopsided beyond a threshold. This response is
  assumed, not estimated from market data, which is why the paper varies it.
- **Controls.** Pre-trade gates (position limits, per-order and aggregate size caps,
  layer limits, self-match prevention, a cancellation throttle) and surveillance
  detectors (spoofing, layering, self-match and excessive-cancel patterns).
- **Traders.** Fixed scripts: legitimate makers and takers, plus scripted
  spoofing-like and layering-like positive controls.
- **Replay.** Every run saves its plan, seeds, source-code hashes and recorded
  actions. A `verify` command replays the saved actions and checks that the event
  hashes and summary statistics match exactly.

See the [TradeGauntlet workflow guide](docs/tradegauntlet.md) for how the modules fit
together.

## Repository layout

| Path | Contents |
| --- | --- |
| `pilot/` | Simulator core: matching engine (`market.py`), scripted traders (`strategies.py`), gates and detectors (`revision.py`, `detectors.py`), and the earlier pilot studies. |
| `paper_study/` | Runners for the paper's sensitivity and boundary experiments, plus reporting. |
| `agent_study/` | The pending LLM-agent study: closed-loop runner, scenarios, spending limits, replay and review export. See its [README](agent_study/README.md) and [protocol](agent_study/PROTOCOL.md). |
| `experiments/` | Earlier experiment framework and Anthropic API adapter used by the v0.4 smoke test. |
| `tests/` | Unit and integration tests (112 tests). |
| `results/` | Saved evidence for every reported experiment: plans, configs, traces, summaries and manifests. |
| `paper/arxiv/` | The paper in the arXiv LaTeX template (`main.tex`); edit this from now on. See its [README](paper/arxiv/README.md). |
| `paper/offline_study/` | Original v2.4 build of the paper (`build.py`, `content.py`, references, figures) and its review records. |
| `output/pdf/` | Rendered papers: v2.4 in the arXiv template (`tradegauntlet_arxiv.pdf`), the original v2.4 layout, and the earlier v0.4 pilot report. |
| `docs/` | Workflow guide, pilot protocol, commercial assessment, version history and historical documents. |
| `provenance/` | Records of how this repository was separated from its original home. |
| `scripts/` | Command-line tools for the original pilot: replay, package verification, the invariant check, result comparison and the v0.2/v0.4 document builders. Run them from the root as `python -m scripts.<name>`. |

The saved results the paper relies on are:

| Directory | Episodes | Used for |
| --- | --- | --- |
| `results/sensitivity-v1` | 2,140 | Cap/response and throttle/session-length sensitivity |
| `results/boundary-v1` | 560 | Checks on either side of the response and cadence boundaries |
| `results/reporting-v2_3.json` | derived | Means, ranges and rejection causes reported in the paper |
| `results/offline-v2` | 1,120 | Original v0.2 pilot (160 calibration, 960 evaluation) |
| `results/adversary-v1` | 24 searches, 7,600 held-out and confirmation episodes | Adaptive adversary and 100-seed confirmations with bootstrap intervals |
| `results/lobster-calibration-v1` | derived | Imbalance-to-price slopes fitted to the five LOBSTER samples (AAPL, AMZN, GOOG, INTC, MSFT) (data not included) |
| `results/execution-quality-v1` | 6,000 | Fill rate, shortfall, edge, markout and inventory of the legitimate scripts under each control |
| `results/abides-check-v1` | 200 ABIDES sessions | Split-display gain in the ABIDES RMSC04 market (needs ABIDES installed) |
| `results/agent-phase2-mock-v1` | 128 | Software check of the LLM runner with scripted mock policies (no LLM calls) |

## Reproducing the results

The simulator, tests and replay need only the Python standard library. The saved
evidence was produced with **CPython 3.9.6**, and the exact-match verify commands
check the interpreter version, so use 3.9.6 for verification. One way to get it:

```sh
uv python install 3.9.6
PY="$(uv python find 3.9.6)"
```

From the repository root:

```sh
# Run the test suite
$PY -m unittest discover -s tests -v

# Replay and verify the paper's saved evidence
$PY -m paper_study.sensitivity verify results/sensitivity-v1
$PY -m paper_study.boundary verify results/boundary-v1
$PY -m paper_study.reporting results/reporting-v2_3.json --verify
$PY -m paper_study.adversary verify results/adversary-v1   # about 5 minutes on 4 cores

# Verify the original pilot and the bounded invariant check
$PY -m scripts.verify_package
$PY -m scripts.replay results/offline-v2
$PY -m scripts.check_invariants
```

To run an experiment from scratch, plan, run and verify it in a new directory
(runners refuse to overwrite existing output):

```sh
$PY -m paper_study.boundary plan results/local-boundary
$PY -m paper_study.boundary run results/local-boundary
$PY -m paper_study.boundary verify results/local-boundary
```

The tests and the sensitivity replay also pass on Python 3.13, but the boundary
runner refuses other interpreter versions and the derived report differs slightly,
so use 3.9.6 when checking saved evidence. Instructions for rebuilding the paper PDF are in
[paper/offline_study/README.md](paper/offline_study/README.md).

## The LLM-agent study (in progress)

The next phase gives LLM agents legitimate trading tasks (execution and market
making) and measures whether they produce concerning order patterns, and how
controls change their behavior and costs. The runner in `agent_study/` feeds each
model decision the live book, inventory and recent events; uses separate
development and held-out scenarios; enforces a spending and call limit with no
automatic retries; and saves every request and response so runs can be replayed
without calling the model again.

So far only a 128-episode mock check has run. The first paid validation run is
capped at US$10 and is waiting on provider access. No LLM findings are reported.

## Scope and limitations

- Prices are synthetic and the price response is assumed. Results describe the
  implemented rules inside this simulator, not how often these patterns occur in
  real markets.
- One account and one instrument, with no exchange latency, strategic background
  traders, auctions or cross-venue routing.
- The scripts, labels and detectors were developed together, and the sensitivity
  and boundary settings were chosen after earlier results were known. The analyses
  are exploratory, not preregistered.
- An alert records a pattern; it is not a finding of unlawful conduct.

## More documentation

- [Version history](docs/changelog.md): what changed in each paper and pilot version.
- [Paper source and reproduction details](paper/offline_study/README.md).
- [Pilot protocol (v0.2)](docs/pilot-protocol.md) and the earlier
  [v0.4 pilot report](output/pdf/agentic_compliance_corrected.pdf).
- [Commercial assessment](docs/commercial-validation.md): desk research on whether
  predeployment testing for trading agents is a viable product. No customer
  validation has been done.
- [Historical documents](docs/history/README.md) and [provenance](provenance/README.md).

This project is independent of [TrustBound](https://github.com/tokencraftdata/trustbound)
and [Agent Wallet Policy Privacy](https://github.com/tokencraftdata/agent-wallet-policy-privacy),
which are separate research projects.

## Citation and license

If you refer to this work, please cite the paper:

> Sandip Nandi. *TradeGauntlet: Testing the Limits of Trading Compliance Controls.*
> Exploratory technical report, version 2.4, 10 November 2026.

The code and data in this repository are released under the [MIT License](LICENSE).
