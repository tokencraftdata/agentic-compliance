# Phase 2 software check — no LLM inference

128 development episodes, 768 scripted decisions, zero provider calls and US$0
provider spending. All 128 episodes replayed with no request, action, event or
metric mismatch. All 106 unit tests passed using Python 3.9.6.

The two mock policies test plumbing, not LLM behavior. No held-out model run,
independent external validation or human labeling was performed. These records
are not additional findings for the paper.

The frozen protocol names source commit
`49a75af` and contains the full source hashes, task instructions, prompts,
scenario definitions and condition grid. `manifest.json` covers the original run
files; the subsequent test log and verification report are accompanying checks.
See `verification.json` for the replay result and test-log hash.

From the repository root, with the recorded source files:

```sh
python3 -m agent_study.runner verify results/agent-phase2-mock-v1
```

The [study instructions](../../agent_study/README.md) describe a fresh mock run,
provider access requirements and the limits on interpreting future results.
Historical code, paper content and PDFs are unchanged from main commit
`18cbfc029bd50c6feb9b1d0eb5dd4e98a11990a8`.
