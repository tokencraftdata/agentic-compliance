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

Version 1.7 changes the formatting to a continuous academic article layout.
The complete manuscript text, references, experiment code, and recorded results
are compared against version 1.6. Only page-break directives, subsection styling,
and the source snapshot identifier differ in the structured manuscript. This is
a presentation revision; it does not add scientific validation.

Version 1.8 revises the abstract, introduction, related-work discussion, general
control discussion, and conclusion around the observed failures. It shortens
section 10.1 and moves implementation/replay instructions to the repository.
No appendix is added. The market mechanics, methods and results (sections 2.2-8),
FINRA subsection, limitations, and assistance disclosure remain unchanged.
Published ACM versions replace references 2 and 4; the remaining publication
checks and access limitations are recorded in `publication_review.json`.

## Verdict

**Revised assessment after substantive review: do not submit this draft yet.**
The earlier readiness judgment gave too much weight to arithmetic consistency,
replay, and disclosure. The economic endpoint is heavily conditioned by the
engine's activation threshold; the cadence result follows the detector's timing
condition; and the throttle window exceeds every studied session. These are
useful regression examples but insufficient support for broader control claims.
See `MAJOR_REVIEW_RESPONSE.md` for verified findings and the analyses still needed.

Version 1.9 only shortens section 10.1 to a pinned repository link and private-access
note. It does not claim to resolve the methodological review. The conclusion,
methods, results, references, and AI-assistance disclosure are otherwise unchanged.
The repository now includes a separate diagnostic that replays the 40 gated
responsive parameter cases and checks their full-event hashes while observing
response activation. This is a diagnostic of existing evidence, not held-out
validation or a completed sensitivity sweep.

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
