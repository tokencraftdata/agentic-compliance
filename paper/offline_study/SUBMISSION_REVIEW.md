# Final submission assessment — 9 November 2026

Assessed as an exploratory technical report or workshop-style methods paper.
The author now plans an arXiv preprint. This is an internal technical and
editorial review, not external peer review. The arXiv preparation and research
plan is in `ARXIV_PLAN.md`.

Version 1.4 makes trading compliance the explicit purpose of the candidate-control
evaluation. It changes the title and framing, with no new experiments or results.
The paper separates the tested engineering controls from legal compliance,
human review workflows, and oversight of LLM trading agents.

Version 1.5 addresses the subsequent literature and regulatory-positioning review.
It adds ten references, compares the nearest agent-compliance and constrained
execution work, and distinguishes FINRA context from the tested engineering
policies. No new experiment or legal-compliance finding is reported.

Version 1.6 has been reviewed again for submission. It defines offline execution
and scripted policies separately, keeps the experimental findings unchanged,
and supplies a copyable arXiv submission sheet. The final prose pass removes
generic transitions and repeated novelty disclaimers from the related-work
discussion while preserving its substantive comparisons. The internal release version is
kept in the repository rather than presented as an assigned arXiv version.

The deeper research review additionally checks five central claims directly
against saved episode records (`audit_claims.py` and `research_review.json`), adds
Gu et al. (ICAIF 2024) on liquidity dependence, and distinguishes a proposed
cryptographic audit pipeline from an implemented one in the cited execution work.
No numerical mismatch was found. The remaining weaknesses are scientific scope
limits, listed below, rather than hidden completed experiments.

## Verdict

With the focused related-work and FINRA revision completed, the manuscript is
prepared for an arXiv submission within its stated exploratory scope, subject to
the author and account steps below. This assessment does not predict moderation
or substitute for external scientific review.

The scientific claims are adequately supported for the paper's stated offline,
scripted scope. The manuscript is ready to circulate as a technical report and is
a plausible workshop submission after venue preparation. It should not be
submitted as evidence of emergent LLM manipulation or as a broadly validated
production defense.

For a stronger full research submission, the main gaps are independent strategy
families and negative controls, evaluation in a richer or externally calibrated
market, and an independent detector or expert-adjudicated comparison. The current
window study is post-hoc and its 15-case recall result is tuning performance.

## Checks completed

For version 1.6, the unchanged experiment code, saved evidence, figures, and
quantitative manuscript sections were compared with version 1.5. The 80-test and
360-episode replay results below are from that preceding validation; they were
not rerun for the scope clarification. Source/evidence hashes and the rebuilt
PDF were checked anew.


Version 1.5 checks include source verification, comparison of unchanged experimental
sections and measurements against version 1.4, numeric replay, and inspection of
every page of the rebuilt PDF. Current machine-readable records accompany the PDF.


- Re-ran all 80 tests successfully.
- Replayed all 360 new episodes with no mismatches; recomputed CSV rows and summary.
- Checked the original pilot source hashes and all new study evidence hashes.
- Audited the six-by-ten result, the 40 matched economic comparisons, and the
  four cadence rows at the 180-second window against saved measurements.
- Confirmed that all 40 matched gated responsive/unresponsive full-event hashes
  are equal, as well as their reported wealth.
- Confirmed that the aggregate gate's rules are identical in the two conditions;
  clarified that this does not generally force realized fills or fees to be equal.
- Replaced repetitive discussion openings with direct explanations. Added an
  explicit distinction between legitimate order splitting and the tested script.
- Checked the final PDF by rendering every page. No placeholder results or
  drafting instructions remain.

The prose is specific to the implementation and observed failures. This review
does not infer authorship from writing style or claim to predict an AI detector.

## Before the arXiv submission

1. Author details are now confirmed: Sandip Nandi, Independent Researcher.
   Version 1.2 replaces the anonymous label. The author should read the final
   paper and confirm its content before submission.
2. Use q-fin.TR as the proposed primary category; check account endorsement
   status. Category selection remains subject to arXiv moderation.
3. Decide how readers can access the artifact. The repository remains private;
   its visibility was not changed. Public artifact availability is a recommended
   reproducibility improvement, not stated here as an arXiv acceptance rule.
4. Review the AI assistance disclosure added in version 1.2 and corrected in
   version 1.3 to name both coding assistants used. arXiv requires
   significant generative AI use to be reported. The disclosure must accurately
   describe the assistance, not claim that no AI was used to prepare the paper.
5. Choose the distribution license and complete arXiv's upload/preview checks.
   Version 1.2 embeds outline fonts for the PDF submission route. This paper is
   generated with ReportLab, not TeX; any later TeX version must include its source.

For a later double-blind venue, prepare a separate anonymized artifact and apply
that venue's template and disclosure rules. This manuscript's GitHub owner link
identifies the project and is not appropriate for a fully anonymized submission.

No additional experiment is claimed to have been run during this final wording
pass. Existing data, detector algorithms, and historical reports are preserved.
