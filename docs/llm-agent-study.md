# LLM-agent study v1: run book

This is the run plan for the first live LLM-agent study with `agent_study/`. It
covers four stages: a development check, a development pilot, a held-out
evaluation on Claude Haiku 5.5, and a smaller held-out subset on Claude Sonnet 5.5.
No result exists until the stages below have run and verified.

## Grid

| Stage | Model | Factors | Episodes | Calls | Est. USD | Cap USD |
| --- | --- | --- | --- | --- | --- | --- |
| 1 dev check | Haiku 5.5 | stationary; 2 tasks; task_only; gate off/aggregate; response off/on (step 0.4); seed 11000; 360 s | 8 | 48 | 0.01 | 0.25 |
| 2 dev pilot | Haiku 5.5 | stationary, deep_book; 2 tasks; 2 prompts; gate off/legacy/aggregate; response off/on (step 0.4); seeds 11001-11002; 360 s | 96 | 576 | 0.15 | 0.50 |
| 3 held-out | Haiku 5.5 | liquidity_withdrawal, trend_reversal, price_jump; 2 tasks; 2 prompts; 3 gates; response off/on x (step 0.4, linear 3); seeds 21000-21009; 720 s | 1,440 | 17,280 | ~4 | 8 (0.80 per seed shard) |
| 4 held-out | Sonnet 5.5 | as stage 3, gates off/aggregate, step 0.4 only, seeds 21000-21004 | 240 | 2,880 | ~13 | 25 (5 per seed shard) |

Each held-out stage is split into one plan per seed, so every shard runs in under
two hours, has its own ledger, and verifies on its own. `scripts.agent_report`
pools shards and reports paired contrasts with 95% cluster-bootstrap intervals
over the 30 held-out market paths (family x seed).

Prices are the standard rates on the [pricing page](https://platform.claude.com/docs/en/about-claude/pricing),
checked 2026-11-12: Haiku 5.5 $0.10 / $0.50 and Sonnet 5.5 $2 / $10 per million
input/output tokens. The ledger reserves each call's full context window, so a
Haiku call holds about $0.10 and a Sonnet call about $2 until its usage receipt
settles. Per-shard caps therefore cover at least one reservation.

## Environment

- Python 3.9.6 (`uv python install 3.9.6`), the interpreter of the existing evidence.
- The key is read from `TG_ANTHROPIC_API_KEY`, falling back to `ANTHROPIC_API_KEY`.
  The base URL is fixed at `https://api.anthropic.com`; `ANTHROPIC_BASE_URL` is ignored.
- Thinking is turned off for every decision call: Haiku 5.5 with `thinking: disabled`,
  Sonnet 5.5 with `thinking: between_tools` (Sonnet 5.5 rejects `disabled`; no tools are
  sent, so it does not think). Each plan's model entry records this, so it is frozen.
  The first stage 1 run, with default thinking, had 36 of 48 replies unusable (thinking
  used up the 512-token limit, or JSON came wrapped in a Markdown fence). It is kept as
  `1-dev-check-attempt1`. The runner now also accepts one surrounding code fence.
- Budget ledgers append one changed entry per line (earlier ledgers rewrote the full
  state each time, about 1 GB per held-out shard). Old ledgers still load. The stage 1
  and 2 development results verify against the source at the commit that added them
  ("Turn off model thinking for live calls; run development stages"); the stage 2
  ledger from that format is kept xz-compressed.
- HTTP 429 and 529 are retried up to six times with backoff; they are not billed.
  Every other provider failure stops the shard and keeps its reservation.

## Steps

All from the repository root, with `PY=$(uv python find 3.9.6)`.

```sh
# 0. Tests, then plans (re-check the prices first; the runner refuses a stale date)
$PY -m unittest discover -s tests
$PY -m scripts.agent_plans agent_study/plans/llm-v1 --prices-checked
R=results/agent-llm-v1; mkdir -p $R/protocols $R/ledgers

# 1-2. Development (freeze, run, verify)
for s in 1-dev-check:0.25:48 2-dev-pilot:0.50:576; do
  n=${s%%:*}; rest=${s#*:}; cap=${rest%%:*}; calls=${rest#*:}
  $PY -m agent_study.protocol agent_study/plans/llm-v1/$n.json $R/protocols/$n.json
  $PY -m agent_study.runner run $R/protocols/$n.json $R/$n --max-calls $calls \
      --allow-live --budget-ledger $R/ledgers/$n.jsonl --max-usd $cap
  $PY -m agent_study.runner verify $R/$n
done

# 3-4. Freeze every held-out shard, commit and push the protocols BEFORE any held-out call
for p in agent_study/plans/llm-v1/[34]-*.json; do
  $PY -m agent_study.protocol $p $R/protocols/$(basename $p)
done
git add $R/protocols agent_study/plans/llm-v1 && git commit -m "Freeze held-out LLM-agent protocols" && git push

# then, per shard (a few shards at a time, within the account's rate limit):
$PY -m agent_study.runner run $R/protocols/3-heldout-haiku-21000.json $R/3-heldout-haiku-21000 \
    --max-calls 1728 --allow-live --budget-ledger $R/ledgers/3-heldout-haiku-21000.jsonl --max-usd 0.80
#   Sonnet shards: --max-calls 576 --max-usd 5
$PY -m agent_study.runner verify $R/<shard>

# Report
$PY -m scripts.agent_report $R/3-heldout-haiku-* --out $R/report-haiku
$PY -m scripts.agent_report $R/4-heldout-sonnet-* --out $R/report-sonnet
```

Development results may change prompts or settings, which means editing
`agent_study/` and re-freezing. Held-out outcomes must not. A shard that stops on
a provider failure is kept as it is and reported with its missingness. It is
not deleted and replaced by a cleaner rerun.

## Interpretation limits

The market remains a simulation. Alerts are rule observations, not findings of
misconduct. All held-out families were authored in this project, so this is
structural holdout, not external validation. No human behavior review has been
done.
