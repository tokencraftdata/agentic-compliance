# Standalone offline research paper

**Evaluating Controls for Trading Compliance: An Offline Study of Order Splitting and Cancellation Timing**

This manuscript reports completed scripted experiments only. It replaces the
completed-LLM-study framing of the earlier full-paper draft. Version 1.5 is a
preprint prepared for arXiv with the confirmed author, Sandip Nandi (Independent
Researcher). Preparation is not a claim of submission, public release,
preregistration, or peer review.

The compliance focus is evaluation of candidate technical controls: which
scripted sequences are flagged, whether a gate removes their response-dependent
payoff, and what legitimate execution it obstructs. Version 1.4 makes this purpose
explicit in the title, abstract, introduction, discussion, and conclusion. Version 1.5 adds the closest agent-compliance,
execution-control, and empirical spoofing literature, and a specific FINRA
discussion. The measurements and experiment code are unchanged. Legal compliance, human review
workflows, and LLM trading behaviour have not been evaluated.

The FINRA discussion distinguishes regulatory context from experimental policy
choices and identifies coverage missing from the simulator. Source-check scope
and URLs are in `literature_review.json`; `compliance_revision_check.json` records
the comparison with the previous release.

The rendered paper is `output/pdf/order_splitting_and_timing.pdf` at the repository
root. `build.py` is the authoring source; it generates tables and figures from
`results/paper-review-v1/summary.json`. `manuscript.json` is a readable structured
export of the complete paper. Change prose in `build.py` and rebuild, rather than
editing only the generated export. References and PNG/SVG figures are included.

## Reproduce the new evidence

Run from the repository root with **CPython 3.9.6**, the interpreter used for the
historical and new numeric evidence. The simulator uses the standard library.

```sh
python3 -m unittest discover -s tests -v
python3 -m paper_study.evaluate verify results/paper-review-v1
python3 -m paper_study.evaluate run results/local-paper-review
python3 -m paper_study.evaluate verify results/local-paper-review
```

The new study contains 40 gated unresponsive counterfactuals and 320 legitimate
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

Version 1.2 embeds outline fonts from the pinned matplotlib distribution and
discloses AI assistance in preparing the code and manuscript; version 1.3
corrects that disclosure to name both coding assistants used (Anthropic Claude
Code and OpenAI Codex). The trading
experiments remain entirely scripted. `ARXIV_PLAN.md` records the arXiv
preparation, proposed research sequence, and customer-discovery hypotheses.
The author details are confirmed. License selection, account/endorsement checks,
and the author's final review remain before submission; nothing has been
submitted to arXiv.
