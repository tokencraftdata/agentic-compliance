# Release process

*November 2026.* How a change becomes a version, how a version is checked, rolled out and rolled
back. The pipeline is in `.github/workflows/release.yml`; it has not run against a real AWS
account (registry, roles and clusters are repository variables still to be set).

## 1. Versions

Semantic versioning, `MAJOR.MINOR.PATCH`, in three places that must agree (`release_check.py`
and `tests/test_release.py` verify): `pyproject.toml`, `finbot.__version__`, and the top entry
of `CHANGELOG.md`. Every turn's audit line carries the serving version, so a canary can be
compared with the version it replaces.

| Change | Bump | Examples |
|---|---|---|
| Stored data or wire format, HTTP API, a security guarantee | **major** (minor while 0.x, with upgrade notes) | session id format, `fbenc2:` envelopes, `202` from `/messages` in queue mode |
| New capability, new domain, new setting with a safe default | minor | a domain plug-in, `FINBOT_DISPATCH` |
| Fix with no format or API change | patch | a false block, a lease bug |

Anything that changes stored data or how old and new workers interact gets an **Upgrade notes**
section in the changelog (see 0.4.0: drain old workers first).

## 2. Branches and tags

Trunk-based: short-lived branches, pull requests into `main`, CI (`ci.yml`, `security.yml`) on
every pull request. A release is a tag `vX.Y.Z` on a `main` commit whose changelog entry has
been moved from "Unreleased" to a date. The tag must equal `finbot.__version__`; the pipeline
refuses otherwise.

## 3. The release gate

`python scripts/release_check.py` runs every gate with absolute thresholds; one miss blocks the
release. `--quick` skips the slow gates for a local check; the pipeline always runs all of them
and attaches the report to the release.

| Gate | Threshold |
|---|---|
| version, clean tree | versions agree; nothing uncommitted |
| lint, types, tests | ruff, mypy, full suite: clean |
| evaluation, dev and held-out | every case passes |
| hallucination stress, both data sets | 0 injected hallucinations reach the user |
| false-block probe, both data sets | 0 correct answers blocked |
| consistency | pass^16 = 1.000 |
| red team, single and multi-turn | 0 attacks succeed against the defended configuration |
| help-page audit | 0 findings |
| durability (real processes) | every scenario passes |

## 4. Pipeline

```
tag vX.Y.Z ─► gate ─► image ──────────────► staging ─────────► production (approval)
                │       build, SBOM,          deploy by digest     canary 10% → 50% → 100%
                │       Trivy, push,          after verifying      bake 15 min per step,
                │       cosign sign +         the signature;       automatic rollback
                │       SBOM attestation      reconcile job
                └─► draft GitHub release (changelog section + gate report)
```

- **One image, promoted by digest.** Built once, scanned (HIGH/CRITICAL fail), signed keyless
  with cosign through GitHub's OIDC, with the SBOM attached as an attestation. Staging and
  production verify the signature before deploying and deploy the digest, never a tag.
- **Approvals.** The `production` environment has required reviewers; that review is the
  release approval. Nobody deploys to production from a laptop.
- **Configuration is not code.** Guard rules go through the adaptive controller's own gate,
  shadow and canary (`adaptive/controller.py`); model drains and routing go through the control
  plane. Neither needs a release, and a release never changes them.

## 5. Canary and automatic rollback

`scripts/rollout.py` shifts traffic by replica count between `finbot-worker` (stable, label
`track: stable`) and `finbot-worker-canary` (`deploy/k8s/worker-canary.yaml`); the queue spreads
turns over all pods. After each step it bakes, then `finbot/rollout.py` compares the canary with
the baseline:

| Signal | Rolls back when |
|---|---|
| card data in an outgoing answer (`egress_leak`) | any, immediately, without waiting for more traffic |
| unsafe output (planted links, secret requests, contact lures) | above twice the baseline and above 0.5% |
| "try again" turns (`unavailable`) | 2 points above the baseline |
| refusals and blocked answers | 5 points above the baseline |
| p95 turn latency | 1.5x the baseline |
| fewer than 200 turns at this step | wait and bake again (up to 3 times), then roll back |

Rollback sets the canary to 0 replicas and leaves the stable Deployment and its image untouched.
Metrics come from the turn audit lines (which carry the version) and security events, through a
command the deployment provides (`CANARY_METRICS_CMD`; `metrics_from_audit` shows the shape).

## 6. Data and format changes: expand, migrate, contract

A change to stored data must work with old and new code side by side, because a rollout runs
both:

1. **Expand:** new code reads old and new formats, writes the old one (or both).
2. **Migrate:** once every pod runs the new code, switch writes to the new format.
3. **Contract:** a later release stops reading the old format, after data written in it has
   expired or been rewritten.

0.4.0 examples: `fbenc2:` envelopes are written now and `fbenc1:` still decrypts (expand and
migrate in one step, because no old reader writes `fbenc2:`). The fixed 24-hour journal lifetime
could not be made side-by-side safe, so 0.4.0 requires draining old workers first; that is why
it is called out in the upgrade notes. Session state lives at most 24 hours, so a contract step
can follow one day after the migrate step.

## 7. Rollback and hotfix

- **During a canary:** automatic (section 5).
- **After full rollout:** run `scripts/rollout.py` with the previous signed digest (the same
  canary steps, in reverse direction of risk), unless the release had a non-reversible upgrade
  note; those releases state the rollback limits in the changelog. A manual-trigger job for this
  in `release.yml` is not built yet.
- **Hotfix:** a branch from the release tag, the fix plus a regression test, a patch version, the
  full gate. No gate is skipped for a hotfix.
- **Model or guardrail emergencies** are not code rollbacks: drain the model on the control plane,
  or roll the guard policy back with the controller (one call each).

## 8. After a release

- The reconciliation job runs in staging as part of the pipeline and daily in production:
  tickets vs records, audit vs commits, index vs source.
- Watch the canary signals for a day at 100%: refusal and handoff rates, `unavailable`,
  security events by class, model failovers, tokens per turn.
- Move the changelog's next section to "Unreleased".

## 9. Checklist

- [ ] Changelog entry dated, with upgrade notes for any format, API or security change
- [ ] Versions bumped in `pyproject.toml` and `finbot/__init__.py`
- [ ] `python scripts/release_check.py` passes locally
- [ ] Pull request merged to `main`; CI and security scans green
- [ ] Tag `vX.Y.Z` pushed; release gate green; image signed
- [ ] Staging deploy and reconciliation green
- [ ] Production approval given; canary promoted; draft GitHub release published

## 10. Not done yet

- The pipeline has never run: set the registry, role and cluster variables, and the canary
  metrics command, before the first release.
- Dependencies are not hash-pinned, and the base image is not pinned by digest.
- The API Deployment has no canary twin yet; API pods are rolled with a normal rolling update.
