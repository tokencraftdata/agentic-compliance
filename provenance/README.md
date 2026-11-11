# Provenance

Records of where this repository's code and evidence came from.

- `REPOSITORY_MIGRATION.json`: file list and hashes from the 8 November 2026 split
  out of the TrustBound repository.
- `RELEASE_MANIFEST.json`: hashes for the v0.2 pilot release.
- `REVIEW_RELEASE_MANIFEST.json`: hashes for the v0.4 review release.
- `README.pre-separation.md`: the README as it stood before the split.

These manifests record files at the paths they had when written. Several documents
have since moved into `docs/` and the root scripts into `scripts/`; the code and `results/` paths are unchanged, and
each experiment's own `manifest.json` under `results/` is what the verify
commands check.
