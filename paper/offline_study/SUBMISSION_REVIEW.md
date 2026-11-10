# Submission assessment - 10 November 2026

Version 2.2 retains the assessment of version 2.0 as an exploratory computational methods report.
The paper is not a validated agentic-compliance system or an LLM trading study.

## Scope of version 2.2

Adds five published papers: Pandey and Rajwal (ICAIF 2025), RISCTrade (ICIC 2026,
Springer proceedings citation year 2027, first online July 2026), InvestorBench
(ACL 2025 long paper), FinAgent (KDD 2024), and QFinZero (ACL 2026 system demo).
The two relevant finance-journal articles remain references 19 and 20.

The abstract, introduction, related work, practical discussion, limitations and
conclusion now identify the contribution as controlled comparisons of interacting
compliance controls and their execution costs. The paper does not claim the first
LLM-compliance benchmark. Actual-agent adaptation is a proposed extension with
results explicitly pending. Mock software checks are not included as LLM findings.
The tables, figures, numerical claims and underlying experiment sources are unchanged.

## Scope of the preceding version 2.1

Adds Robinhood as a dated industry example using two official company sources,
and explains how the scripted cases could support control regression testing.
The company citations motivate the study; they do not validate its methods or
establish demand. No experiment, result, detector definition, table, or figure
has changed. No Robinhood system or customer activity has been evaluated.

## Scientific assessment

The previous positive judgment was too strong because the original zero-gain
result was largely determined by the response threshold, the throttle could not
expire within a session, and legitimate execution losses were underplayed.
Version 2.0 addresses these issues with new comparisons and narrower claims:

- The cap-threshold grid shows that the same 20-lot cap yields zero response
  benefit at threshold 0.4 but 48 tick-lots at 0.2 for the fixed split probe.
  That 48 is itself predictable from 12 cycles x 2 lots x 2 ticks; the paper
  explicitly interprets this as model dependence, not market validation.
- Twelve fresh parameter vectors are each crossed with five market seeds.
  At threshold 0.2, 30/60 aggregate-gated pairs retain positive response-dependent
  wealth without an alert; at 0.4 none do. Throttling is disabled in these
  comparisons to isolate the cap-response interaction.
- A one-hour horizon allows all ten aggregate-gated cases at the 1,800-second
  throttle window to recover submissions at least once, yet mean volume falls
  from 300.6 to 74.4 lots (75.2%). Recovery is not absence of material cost.
- The existing 14.1% and 28.2% legitimate-maker costs and the new 75.2% cost are
  in the abstract. Rejection measures and gate arms are explicitly labeled.
- The cadence staircase is presented as a property of the rule and an authored
  legitimate counterexample, not an operational false-positive-rate estimate.
- Compact formal detector conditions replace ambiguous prose. Results are
  organized around three findings rather than an experiment-development log.
- Three published journal articles and CFTC/EU sources broaden the literature
  and regulatory discussion. The version 2.2 bibliography has 15 journal/conference papers,
  4 arXiv preprints, 2 SSRN working papers, 6 official regulatory sources, and
  2 official company sources for the motivating example.

## Remaining limitations

The added tests do not resolve one-account modeling, the stipulated step response,
lack of calibration or strategic background agents, author-designed labels,
absence of genuinely separate test strategy families, or absence of LLM trading.
The new vectors are from the same generator. Shared seeds are paired inputs, not
extra independent samples. The paper states these limits and makes no population
accuracy or production-defense claim.

The contribution is modest: a transparent case study of pitfalls in evaluating
interacting controls. It can be released as an exploratory technical report with
these claims. I would not present it as a strong standalone empirical-finance or
agentic-AI result. More peer-reviewed references strengthen its context, not its
external validity or novelty.

## Validation and release status

- The original 82 unit/integration tests passed, including the strict threshold boundary and
  separation of aggregate and individual size limits.
- 40 original event hashes matched before the new study ran.
- 2,140 new episodes replayed without a record mismatch; summaries recomputed.
- Central paper numbers independently aggregated from saved records.
- PDF and metadata checks are recorded in `arxiv_pdf_check.json`; visual checks
  are recorded in `visual_qa.json` after rendering.

No arXiv submission has been made. The human author still needs to read the final
paper, choose a distribution license, and check account/endorsement requirements.
The repository remains private. A public reviewed snapshot or reviewer access is
needed for independent reproduction; an anonymous mirror is an option only if a
later venue requires blind review. No public-access claim or visibility change
has been made. Artifact availability is distinct from arXiv's submission rules.
