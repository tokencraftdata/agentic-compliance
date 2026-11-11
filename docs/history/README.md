# Historical documents

These files describe earlier stages of the project and are kept for traceability.
They are not the current description of the work; start from the
[main README](../../README.md) instead.

- [original-design.md](original-design.md): the first *Accidental Spoofers* study
  design, written before any code. Its hypotheses about LLM agents have not been tested.
- [revision-notes-v0.2.md](revision-notes-v0.2.md): how the v0.2 pilot responded to
  the first review.
- [review-revision-v0.4.md](review-revision-v0.4.md): findings and reproduction
  commands for the v0.4 pilot revision.

Commands in these files assume the repository root as the working directory. File
names that were at the root at the time (for example `PROTOCOL.md` or
`RELEASE_MANIFEST.json`) now live in `docs/` and `provenance/`.

The root-level scripts these documents mention (`replay.py`, `verify_package.py`,
`check_invariants.py`, `compare_results.py`, `review_sensitivity.py` and the
document builders) now live in `scripts/`. Run them as, for example,
`python3 -m scripts.replay results/offline-v2`.
