# Standalone offline research paper

**TradeGauntlet: Testing the Limits of Trading Compliance Controls**

Version 2.4 is a revised exploratory methods paper by Sandip Nandi, Independent
Researcher. It adds cap/response and throttle/horizon sensitivity analyses in
response to the scientific review. The latest findings narrow the protection
claim and foreground legitimate execution costs. It is not a completed LLM study
or a real-market compliance validation.

Version 2.4 renames the workflow **TradeGauntlet** and includes it in the main
title and Section 3 heading. This is a naming revision only; the numerical
results, definitions, references and limitations are unchanged from v2.3.

Version 2.3 adds 560 off-boundary episodes, mean/minimum/maximum summaries,
rejection causes, named seed cohorts, and the explicit gate-detector inequality.
It introduces **ConductLab** as the name for the existing custom simulator and
replay workflow. Prose is revised for clarity; references, limitations and the
AI-use disclosure remain. The LLM study is pending. See
`REVIEW_RESPONSE_v2_3.md` and `language_review_v2_3.json`.

Version 2.2 added five published financial-agent/compliance papers and clarified
the contribution: controlled comparisons of cap-response dependence, throttle
cost/recovery, and detector timing. LLM-agent results remain pending. Version 2.1
added the Robinhood example. Neither revision changes the numerical experiments,
results, tables, or figures, or evaluates Robinhood or an LLM trader.

The bibliography now contains fifteen journal/conference papers, four arXiv preprints,
two SSRN working papers, six official regulatory sources, and two official
Robinhood sources used as industry motivation. Published versions
are cited where verified. `publication_review.json` records publication checks,
new journal papers, and access limitations.

`build.py` defines the layout, references, and figures; `content.py` contains the
prose and evidence-derived tables. They produce `manuscript.json` and the PDF at
`output/pdf/order_splitting_and_timing.pdf`. Source, evidence, and output hashes
are recorded in `document_manifest.json`. No appendix or long reproduction
section is included in the paper; the instructions are here.

## Reproduce the boundary checks and result ranges

Use **CPython 3.9.6** from the repository root:

```sh
python3 -m paper_study.boundary verify results/boundary-v1
python3 -m paper_study.reporting results/reporting-v2_3.json --verify
```

To run a new copy, use a fresh directory:

```sh
python3 -m paper_study.boundary plan results/local-boundary
python3 -m paper_study.boundary run results/local-boundary
python3 -m paper_study.boundary verify results/local-boundary
```

The plan was saved before execution after the earlier findings were known;
it was not preregistered or committed before execution. It adds 480 price-boundary
and 80 cadence-boundary episodes. The exact-time scheduler first reproduces all
40 existing 180-second cadence event hashes. Saved attempts then replay with
zero mismatches. `paper_study.reporting` derives descriptive ranges and exclusive
rejection categories from the original saved records, without rewriting them.
The [workflow guide](../../docs/tradegauntlet.md) identifies the implementation modules.

## Reproduce the sensitivity evidence

Use **CPython 3.9.6**, from the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 -m paper_study.sensitivity verify results/sensitivity-v1
python3 paper/offline_study/audit_sensitivity.py
```

To regenerate, select a fresh output directory:

```sh
python3 -m paper_study.sensitivity plan results/local-sensitivity
python3 -m paper_study.sensitivity run results/local-sensitivity
python3 -m paper_study.sensitivity verify results/local-sensitivity
```

The source and plan were committed at `a5f4cb5` before execution. This is a
post-review, internally fixed plan, not preregistration. The new engine first
reproduces all 40 historical gated responsive event hashes at the original
settings. It then runs 1,440 cap-grid episodes, 480 parameter-replication episodes,
and 220 throttle/horizon episodes. Verification replays recorded actions without
calling the strategy and checks complete records and full-event hashes. Results
and the independent central-number aggregation are in `verification.json` and
`research_review_v2.json`, respectively. No live market or LLM provider is used.

No old experiment sources or historical results are rewritten. Extending the
session horizon and crossing parameter vectors with multiple seeds do not supply
independent strategy families or market calibration. See `SUBMISSION_REVIEW.md`
for the remaining research limits and `arxiv_submission.txt` for draft metadata.

## Reproduce the earlier counterfactual and cadence evidence

Run from the repository root with **CPython 3.9.6**, the interpreter used for the
historical and new numeric evidence. The simulator uses the standard library.

```sh
python3 -m unittest discover -s tests -v
python3 -m paper_study.evaluate verify results/paper-review-v1
python3 -m paper_study.evaluate run results/local-paper-review
python3 -m paper_study.evaluate verify results/local-paper-review
```

This earlier study contains 40 gated unresponsive counterfactuals and 320 legitimate
maker episodes: four re-quote cadences x two gates x 40 reused seeds. It is
explicitly post-hoc. It checks historical hashes, uses frozen configurations,
and never overwrites evidence. Verification replays recorded actions, checks
full-event hashes, and recomputes CSV rows and summaries. The two endpoints are
separate: any gate rejection and response-dependent wealth advantage at the same
gate. A partially rejected but still profitable sequence is not excluded.

Historical `pilot/*.py` modules and results remain byte-identical. The new metric
`max_displayed_per_side` fixes the historical helper's combination of bid and ask
quantity without rewriting old evidence. All three pattern detectors share the
window parameter; the paper documents that behavior rather than inventing an
unchanged independent layering window.

## Replay and historical regeneration details

Each new record stores attempted actions, subject events, rejection reasons,
detector output, and the full event-stream hash. Replay reconstructs the
background events deterministically; the compressed archive does not contain
all background events. Exact numeric reproduction requires CPython 3.9.6;
different Python summation implementations can affect the last bits of
bootstrap summaries. No provider credential or network access is required.

Regenerate into a fresh directory. The new study uses frozen historical cohort
configurations, including the 180-second evasion configuration. The older pilot
regeneration command reads the mutable root configuration; after its default
window changes, it cannot establish byte-identical reproduction of the original
120-second cohort. Historical replay uses each cohort's saved configuration.

## Build the paper

The rendering environment is separate from the numeric interpreter. Use the
pinned rendering dependencies in `requirements.txt` (and the full environment
snapshot in `requirements-render.lock`). The checked artifact uses Python 3.12.

```sh
python3 -m pip install -r paper/offline_study/requirements-render.lock
python3 paper/offline_study/build.py --code-commit FULL_SOURCE_COMMIT_SHA
```

Use the full source commit recorded in `document_manifest.json`, not a branch
name. The manifest links the source, summary, evidence manifest, structured text,
and PDF by SHA-256. Numeric reproduction is deterministic in the pinned numeric
environment; office/PDF rendering across operating systems is not assumed to be
byte-identical. The PDF is checked by rendering and inspecting every page.

The earlier full LLM study remains future work. This paper does not select a model
panel, claim intent annotations, or report customer-demand validation.

The PDF embeds outline fonts. Section 10.2 discloses assistance from Anthropic
Claude Code and OpenAI Codex; the trading experiments remain entirely scripted.
`ARXIV_PLAN.md` records the proposed submission and future research. Author
details are confirmed. License selection, account/endorsement checks, artifact
access, and the author's final read-through remain before submission.
