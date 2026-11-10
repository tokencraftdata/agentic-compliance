# arXiv preparation and next steps

Prepared 9 November 2026. This is a plan, not a submission receipt, external
review, completed customer study, or promise of arXiv acceptance.

## Recommendation

Current title: **Evaluating Controls for Trading Compliance: An Offline Study of
Order Splitting and Cancellation Timing** (version 1.8). The compliance framing
concerns evaluation of candidate technical controls, with the existing scripted
measurements and limitations preserved.

Submit this work as an exploratory computational market-microstructure study
after the author and submission details are finalized. Version 1.5 completes the
focused related-work and FINRA-positioning revision identified in the latest
review; it does not add experiments. Its contribution is a
replayable set of control failures and measurement corrections: split display,
cancellation-throttle interaction, matched economic comparisons, and a legitimate
quoting-cadence counterexample. It does not establish emergent LLM manipulation,
real-world compliance, or commercial demand.

The recommended primary category is **q-fin.TR (Trading and Market
Microstructure)**, whose scope includes automated trading, agent-based modeling,
and market-making. This is a recommendation based on the paper, not a decision
by arXiv. A cs.AI label is not justified merely by the project's future LLM work.
See the [official category taxonomy](https://arxiv.org/category_taxonomy).

Version 1.6 defines offline execution and scripted policy selection separately.
The title, abstract, numerical claims, and proposed category are unchanged.
Use `arxiv_submission.txt` for the final copyable metadata; its fields must match
the submitted PDF. Preparation remains distinct from actual submission.

## Before upload

1. Author details are confirmed: **Sandip Nandi, Independent Researcher**. Version
   1.2 replaces the anonymous label. Confirm the final content before submission;
   the current paper has not been submitted.
2. Have the human author read the paper and inspect the result tables and cited
   sources. The code audit passed 80 tests and replayed 360 new episodes without
   mismatches, but this is not independent scientific peer review.
3. Review section 10.3's AI assistance disclosure. Version 1.3 names both
   coding assistants used on the artifact (Anthropic Claude Code for the
   throttle-isolation, evasion-family, negative-control and window-sweep modules
   and notes; OpenAI Codex for the matched-counterfactual and cadence-grid study,
   the builder, literature checks and manuscript work); the trading policies
   themselves were scripted. Significant generative AI use
   must be reported, and the authors retain responsibility for the paper under
   [arXiv's policy](https://info.arxiv.org/help/moderation/index.html#policy-for-authors-use-of-generative-ai-language-tools).
4. Register or check the author's account and endorsement status for q-fin.TR.
   A first submission or a new category can require endorsement. If requested,
   approach an eligible researcher familiar with the topic and share the paper;
   do not mass-mail endorsers. Follow the [endorsement instructions](https://info.arxiv.org/help/endorsement.html).
5. Choose an artifact-access route: a reviewed public repository snapshot, a
   separate public research archive, or an explicit private-access statement.
   Public reproducibility is recommended; this checklist does not claim it is a
   universal arXiv requirement. Repository visibility has not been changed.
   Preserve the frozen source commit, configurations, evidence, verification
   commands, interpreter versions, and checksums. Choose code/data licensing
   separately from the paper's distribution license.
6. Select the paper's license after considering funder and intended publication
   requirements. arXiv's license for each posted version is irrevocable; the
   author must choose it. See [license guidance](https://info.arxiv.org/help/license/index.html).
7. Upload and preview the paper. The current ReportLab-generated document can
   use the PDF route: one machine-readable PDF, included figures, embedded
   outline fonts, and no JavaScript. If converted to TeX, submit TeX source rather
   than only its output PDF. See [PDF guidance](https://info.arxiv.org/help/submit_pdf.html).
8. Match title, abstract, and author metadata to the final PDF. Suggested
   comments: "11 pages, 2 figures; exploratory scripted simulation
   study." Verify the page count again if the document is edited. Leave journal
   reference and publication DOI empty unless they actually exist.
9. The author completes submission and checks the confirmation/status. arXiv
   moderates submissions and may reclassify or decline them; posting is not
   peer review. After announcement, record the identifier and version in the
   repository. See the [submission overview](https://info.arxiv.org/help/submit/index.html).

## Research direction after the literature review

Keep the project focused on market-conduct control evaluation for autonomous
trading agents. ReguSim already separates agent actions from enforcement and
monitoring; constrained execution with runtime shields also exists. The next
study should test control effectiveness under changes in order representation,
timing, and market conditions, jointly with legitimate execution cost. The
current offline findings supply a baseline, not evidence about LLM behaviour.
See the manuscript references and `literature_review.json` for the primary sources.

## Next research decisions

The following order prioritizes evidence quality over increasing the run count.
It is a proposed work sequence, not completed work or a fixed delivery promise.

| Phase | Work | Evidence required before advancing |
| --- | --- | --- |
| Independent review | Obtain a market-microstructure and a surveillance-methods critique; record disagreements and corrections. | Reviewers can reproduce the central tables and understand the narrow novelty claim. |
| Held-out validation | Freeze endpoints, detector thresholds, and analysis before generating new test data. Use new strategy families as well as new seeds; broaden legitimate execution and maker cadences. Keep development and test families separate. | Report detection, response-dependent wealth, and legitimate execution loss together, including failures. Repeated rescoring is not extra replication. |
| Stronger environment | Add longer sessions covering throttle expiry, variable liquidity, fees and latency, and a calibrated or independent simulator comparison. | Establish which failures survive changes to the stipulated market response. Document disagreements rather than pooling incompatible environments. |
| Model study | Only after the evaluation is credible, freeze actual provider/model versions, prompts, tools, call budget, error handling, and analysis. Compare with scripted baselines and keep invalid calls/missing explanations separate. | Real provider calls and saved traces are required for any LLM finding. Mock calls cannot support it. Obtain a working local credential and agree on spend before execution. |
| Peer-reviewed paper | Select a suitable workshop or journal based on the completed contribution and current venue rules. | A clear claim beyond this exploratory case study, independent evaluation, and venue-specific formatting/disclosure. |

## Customer-demand assessment in parallel

Treat demand as an untested hypothesis. A possible first product is a replay and
regression-testing tool for surveillance and pre-trade controls. This is a
product hypothesis, not a validated market conclusion.

Proposed discovery: 8-12 interviews across surveillance/compliance leads, trading
risk engineers, and surveillance vendors. Ask for a recent control-change or
false-alert incident, how it was tested, analyst time spent, the buyer and budget
owner, available replay data, procurement constraints, and what would make a
pilot useful. Show a concrete replay report only after understanding the current
workflow. Capture contradictory evidence and existing alternatives.

A practical initial decision rule is to continue product development only if at
least two independent organizations agree to a scoped evaluation with a named
owner, usable data or realistic scenarios, and written success measures. Seek
budget evidence through a paid pilot or a specific purchasing process; favorable
comments alone do not establish willingness to pay. These are proposed internal
decision criteria, not statistical estimates of market size.

Keep customer evidence in a separate research log and out of the paper's
scientific results until an actual study supports it. No interviews, outreach,
partnerships, pricing validation, or purchase commitments are claimed here.
