# Revision status - version 2.0, 10 November 2026

The review below is preserved as the record that motivated the revision.
Version 2.0 adds 2,140 sensitivity episodes, with full recorded-action replay;
82 tests pass. The cap/threshold sweep, multi-seed parameter replication, longer
throttle sessions, formal detector definitions, explicit maker costs, three-finding
structure, and CFTC/RTS 6 discussion are now implemented. The cap-response and
parameter comparisons disable the throttle and retain the individual limit at 2x.
Their conditions are stated in the paper.

The new results limit rather than rescue the original protection claim: lower
response thresholds admit positive gains, and throttle recovery can coexist
with large execution losses. The cadence finding is explicitly a rule-boundary
counterexample. Section 10.1 remains just a pinned repository link and access note.
Three additional published journal papers are cited; no preprint is mislabeled
as a journal article. See `SUBMISSION_REVIEW.md` for the current assessment.

Still unresolved: independent strategy families and labels, market calibration,
LLM trading evidence, and external artifact access. These are not claimed as
completed. No anonymous mirror or public repository release has occurred.

---

# Substantive review response - 9 November 2026

Status: further research revision required before recommending arXiv submission.
This supersedes the earlier readiness judgment. No new sensitivity experiment,
LLM trading study, public repository release, or anonymous mirror is claimed.

## What the verification establishes

1. **Economic mechanism.** With symmetric intact background depth of 30 lots
   per side and 20 subject lots on one side, imbalance is 20/(60+20) = 0.25,
   below the engine's strict 0.4 activation threshold. More generally, with
   three background levels of depth D and cap mD, the static value is m/(6+m).
   It exceeds a response threshold t only when m > 6t/(1-t); at t = 0.4 the
   boundary is m = 4. This is not a global bound because trades deplete depth
   and the response uses the book before replenishment. The diagnostic replays
   all 40 aggregate-gated responsive parameter draws, verifies full-event hashes,
   and measures zero response activations, with maximum absolute imbalance
   0.282051. Thus the zero response-dependent wealth result is correct but weak
   evidence of general prevention. A permitted 30-lot market buy produces
   imbalance 1.0 in a separate diagnostic. That is ordinary book depletion,
   not evidence of spoofing or a profitable bypass.

2. **Cadence.** The time window is an explicit necessary condition in the
   detector. Age inside the window is not sufficient: opposite-side fills,
   canceled quantity, fill fraction, distance, and aggregate size also matter.
   The staircase is a rule-boundary demonstration and an authored legitimate
   counterexample, not an estimate of operational false-positive rates. The
   34/40 count is conditional on one script and its chosen paths.

3. **Throttle.** The 1,800-second window exceeds the 720-second session. Once
   rejection prevents accepted submissions from increasing, rejected attempts
   cannot dilute the ratio, and no old submission ages out. The observed lockout
   lasts to session end. The ablation establishes that configuration's cost,
   not a general ranking of the gates.

4. **Legitimate cost.** In the negative-control cohort the large maker falls
   from 56.50 to 48.55 executed lots (14.07%); the patient maker falls from
   66.275 to 47.60 (28.18%). These costs should appear in the revised abstract.
   Table 3 uses the aggregate gate with its throttle enabled. Its 27.95 is
   total rejected actions on seeds 6000-6039; Figure 1's 6.4 is throttle-only
   rejections on seeds 4000-4039. The latter cohort has 27.35 total rejections,
   including 20.8 aggregate-size rejections. These counts are consistent but
   need explicit labels and a common-cohort breakdown in a research revision.

5. **External validity and agents.** One account, one authored generator, one
   market seed per parameter draw, developer-authored labels, and a stipulated
   response do not establish out-of-sample performance. No LLM selects trading
   actions. The current contribution is a scripted control-evaluation study;
   an LLM study remains separate, unexecuted work.

## Analyses and manuscript changes still needed

- Cross aggregate cap multiples with response thresholds on both sides of the
  static boundary. Vary external depth and response strength, including a smooth
  response. Compare matched responsive/unresponsive endpoints at the same gate,
  record activation frequency, and report legitimate volume and rejection cost.
  Run multiple market seeds per parameter vector; freeze a development/test split
  before selecting settings. More seeds from the same generator are not new
  strategy families.
- Cross throttle windows with session horizons long enough for expiry and
  recovery. Include throttle on/off with other controls fixed, and measure time
  to rejection and recovery as well as executed volume.
- Organize the results around three findings: order aggregation and response
  activation; throttle interaction and legitimate cost; detector timing and
  specificity limits. Keep cohort provenance in the repository.
- Give compact formal detector definitions. In particular, distinguish the
  individual size test, the layer detector's distinct-price requirement, and
  the aggregate detector's remaining quantity at a shared fill. The current
  prose should not imply identical filters for every detector.
- Put maker costs in the abstract; label Table 3's gate and rejection measure.
  Treat cadence as a rule property with a concrete legitimate counterexample.
- Keep LLM motivation proportional to the absence of evaluated LLMs. Do not
  relabel scripted policies as agent evidence or imply that more citations
  resolve the experimental design concerns.

## Regulatory additions to consider in that revision

The CFTC's guidance on CEA section 4c(a)(5)(C) makes intent to cancel before
execution material and distinguishes legitimate good-faith cancellation. It
supports the distinction between a surveillance pattern and a legal finding;
these scripts do not establish a violation. This is the U.S. derivatives context,
distinct from FINRA's securities supervision context.

- https://www.cftc.gov/LawRegulation/FederalRegister/FinalRules/2013-12365.html

EU RTS 6 (Commission Delegated Regulation 2017/589) Articles 5 and 7 address
testing methodology and separation of test and production environments. Article
13 is particularly relevant: surveillance adequacy, false alerts, and replay of
order and transaction data. This supplies another motivation for control testing,
not a claim that this simulator satisfies EU obligations.

- https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng

## References and reproducibility presentation

Published versions of Learning Not to Spoof (ACM ICAIF 2022) and ABIDES
(ACM SIGSIM-PADS 2020) are already cited in version 1.8 and retained in 1.9.
The repeat search did not establish an archival conference or journal version
for the six remaining preprints/working papers. An author-reported workshop
presentation is not enough to label The Agentic Regulator a main-conference
publication. See `publication_review.json` for sources and limitations.

Section 10.1 now contains only a repository link and private-access note. Full
instructions remain in the repository README. GitHub metadata confirms that the
repository is private; reviewers cannot independently reproduce results without
access or a released snapshot. An anonymous mirror may suit a later venue that
requires blind review, subject to that venue's policy. No mirror or visibility
change was made. Suggested service: https://anonymous.4open.science/ .

## Reproduce this diagnostic

From the repository root with CPython 3.9.6, choose a new output path:

```sh
python3 -m paper_study.reviewer_diagnostics /tmp/reviewer-diagnostics.json
```

The checked output is `results/reviewer-diagnostics-v1.json`. The runner observes
the existing response decision without changing matching, gate checks, or event
logging; saved full-event hashes must still match. Its single market-order
counterexample is separately labeled and is not added to the paper's episode
count. Existing experiment modules and historical results remain unchanged.
