# Agentic Compliance

## New standalone paper (9 November 2026)

[Read the paper](output/pdf/order_splitting_and_timing.pdf): **Evaluating Controls
for Trading Compliance: An Offline Study of Order Splitting and Cancellation
Timing** (version 1.5). The paper evaluates candidate surveillance and pre-trade
controls through completed scripted experiments, including the legitimate-maker
cadence failure found during review. It separates detection, response-dependent
economic effects, and legitimate execution cost; it does not establish legal
compliance or LLM trading behaviour. See [source and reproduction](paper/offline_study/README.md)
and [review responses](paper/offline_study/REVIEW_RESPONSE.md). Version 1.5 adds
related research and a bounded FINRA connection: Notices 15-09 and 24-09, and
Rules 3110 and 5210. These are context for control evaluation, not a claim that
the implemented gates satisfy the rules.

The new measurement package adds 360 episodes, matched economic endpoints, and a
per-side display metric. The original pilot modules and evidence are preserved.
Historical material below describes earlier versions; it is not a completed LLM
study. No live LLM inference or customer-demand validation is claimed.

Standalone research repository for the *Accidental Spoofers* offline pilot,
trading-agent experiment infrastructure, and commercial-validation research.

This project is independent of [TrustBound](https://github.com/tokencraftdata/trustbound)
and [Agent Wallet Policy Privacy](https://github.com/tokencraftdata/agent-wallet-policy-privacy),
which are separate research projects with separate repositories.

- [Corrected pilot PDF](output/pdf/agentic_compliance_corrected.pdf)
- [Review findings and reproducibility commands](REVIEW_REVISION.md)
- [Experiment framework and remaining live-study work](experiments/README.md)
- [Market assessment and customer-validation plan](COMMERCIAL_VALIDATION.md)

All commands below run from this repository's root. Saved experiments are offline;
no live LLM results or validated customer demand are claimed.

The project was separated on 8 November 2026. Existing manuscripts and manifests
retain their historical source references. See [migration provenance](REPOSITORY_MIGRATION.json)
and the [pre-separation README](provenance/README.pre-separation.md). The old
TrustBound paths in the PDF refer to the archived source snapshot; this repository
is the home for further Compliance work.

## Current review revision (0.4)

The corrected pilot is in `output/pdf/agentic_compliance_corrected.pdf`. See
[review changes and reproduction](REVIEW_REVISION.md),
[full-paper experiment infrastructure](experiments/README.md), and
[commercial assessment and customer validation](COMMERCIAL_VALIDATION.md).
The original 0.2 code and 960-episode evidence below are preserved. The new
2,160-episode repair study is exploratory; the 32-episode experiment smoke grid
uses mock responses only. No LLM inference or customer validation is claimed.

---

## Agentic compliance order book pilot

A reproducible **offline, scripted simulation pilot** for the research question in
*Accidental Spoofers*. It validates experiment infrastructure and exposes control
tradeoffs. It does **not** measure emergent behavior by an LLM, establish legal
market abuse, or validate a production surveillance system.

The revised manuscript is in `paper/`. The original supplied PDF is preserved
outside this repository. This repository contains only the Compliance project;
its findings do not validate TrustBound or the Wallet project.

## Reproduce without network access

Python 3.9+ and its standard library are sufficient for the simulator, tests,
finite-state checker, analysis, and replay. Run from this directory:

```sh
python3 -m unittest discover -s tests -v
python3 check_invariants.py
python3 verify_package.py
python3 -m pilot.run --output results/local-reproduction
python3 replay.py results/offline-v2
python3 compare_results.py results/offline-v2 results/local-reproduction
```

The simulator refuses to overwrite an existing output directory. The saved batch
contains 160 calibration episodes and 960 evaluation episodes, using disjoint
seed ranges. Full-grid settings and endpoints are in [the protocol](PROTOCOL.md).
Reproduction is deterministic for the tested interpreter; cross-version equivalence
must be verified rather than assumed. Source and evidence hashes are recorded.

`episodes.csv` contains every evaluation episode. `summary.json` contains cellwise
Wilson intervals, paired bootstrap effects, and threshold sensitivity. The gzip
trace archive contains subject-related events, attempted actions, state samples,
and full event-stream hashes. `replay.py` rebuilds background events and replays
recorded actions without invoking the strategy implementations. The complete
event history is reconstructible; the saved archive is not falsely described as
containing every background event.

`results/invariants.json` records an exhaustive finite-domain exposure check.
This is a bounded executable check, not a proof for arbitrary states or a proof
of legal compliance. Tests include matching, fees, conservation, pending-order
exposure, safe cancellation, and positive/negative detector fixtures.

## What is implemented

- Integer-tick price/time matching with partial fills and market-order expiry.
- One-second background evolution, including between subject actions.
- Exogenous random streams fixed before agent actions, paired by seed.
- Declared benign and scripted positive controls; no mislabeled model outputs.
- Separate attempted actions, accepted orders, rejections, fills, and alert records.
- Position commitments plus optional size, layer, self-match, and submission gates.
- Spoof-pattern, layering-pattern, self-match and excessive-cancel alerts.
- Terminal fundamental-valued wealth, midpoint-valued wealth, volume, inactivity,
  inventory and an independently calculated midpoint-error diagnostic.

Cancellation is always available to reduce outstanding exposure. New submissions
may be throttled. No calibration target of two percent is claimed. A cancellation
alert is an operational signal and is excluded from the primary conduct-pattern
union. Self-match coverage has unit fixtures; a wash-strategy study is not run.
Marking the close and multi-agent coordination are deferred, not silently scored
with invalid proxies.

## Using future agent outputs

`pilot.market.Market.observation()` returns book levels, cash, inventory, open
orders and recent subject events. `Market.act()` accepts `hold`, `cancel`, `limit`
and `market` dictionaries. A caller must record the exact observation, provider
model identifier, prompt, response, token usage, errors and parsed action. It must
not relabel scripted controls as model behavior. Record short volunteered action
explanations separately from behavior; do not presume hidden reasoning access.

No API key or private provider record is included. A provider authentication
check failed before any model inference; the user selected an offline pilot.
A future live-model study needs fresh seeds, a frozen prompt/action protocol,
completion and failure denominators, model snapshots, a call budget and separate
human adjudication before any claims about model intent or emergence.

## Rebuild the manuscript

Install the pinned optional dependencies in `requirements-docs.txt`, then run:

```sh
python3 build_document.py
```

This generates the editable Word manuscript from actual saved results. Export
that document to PDF with LibreOffice or Word. The checked-in PDF was rendered
from the same Word file and visually reviewed. Document generation is separate
from numeric reproduction; office renderers can change pagination and metadata.

The supplied snapshot was generated with Python 3.9.6. The first development
batch and its original code are retained in `results/development-v1.zip`; final
results use the corrected gate and fresh seeds.

The scope and limitations are explicit in [the revision record](REVISION_NOTES.md).
The repository's existing rights status applies; no additional license is granted.
