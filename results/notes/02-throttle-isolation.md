# Throttle-isolation arm (design doc step: "Throttle-isolation arm")

Scripted, offline, post-hoc mechanism check. Responsive (book-aware) market, fresh
seeds 4000–4039 (n = 40 per cell; 4000–4001 were previously used only by the mock
LLM plumbing smoke test). No LLM inference. Not preregistered.

New arm `aggregate_throttle_off` = v0.4 aggregate gate with the cancel-ratio
throttle disabled (gate config `cancel_min_submissions` set unreachable); size cap,
two-level cap and self-match check unchanged. Detectors use the unmodified config.

## Reproduce

```sh
python3 -m pilot.throttle_isolation run results/throttle-isolation-<new-dir>
python3 -m pilot.throttle_isolation verify results/throttle-isolation-v0.4
python3 -m unittest tests.test_throttle_isolation -v
```

Evidence: `results/throttle-isolation-v0.4/` (480 episodes; verify replays all
480 with 0 mismatches). `results/review-v0.4` still verifies unchanged.

## Results (means over 40 seeds; alerts = episodes alerted / 40)

| Strategy | Gate | Filled lots | Wealth | Price error | Rejected | of which throttle | of which aggregate cap | Legacy alert | Extended alert |
|---|---|---|---|---|---|---|---|---|---|
| large_market_maker | off | 59.48 | 11.05 | 0.1374 | 0.15 | 0 | 0 | 0 | 0 |
| large_market_maker | legacy | 25.48 | 5.10 | 0.0654 | 27.32 | 27.20 | 0 | 0 | 0 |
| large_market_maker | aggregate | 50.92 | 7.65 | 0.1167 | 27.35 | 6.40 | 20.80 | 0 | 0 |
| large_market_maker | aggregate, throttle off | 59.05 | 10.90 | 0.1337 | 24.15 | 0 | 24.00 | 0 | 0 |
| split_3x20 | off | 24 | 24.05 | 0.0666 | 0 | 0 | 0 | 0 | 40 |
| split_3x20 | legacy | 24 | 1.65 | 0.0355 | 5.60 | 0 | 0 | 0 | 40 |
| split_3x20 | aggregate | 24 | −23.95 | 0 | 24 | 0 | 18.40 | 0 | 0 |
| split_3x20 | aggregate, throttle off | 24 | −23.95 | 0 | 24 | 0 | 18.40 | 0 | 0 |
| split_6x10 | off | 24 | 24.05 | 0.1331 | 0 | 0 | 0 | 0 | 40 |
| split_6x10 | legacy | 24 | 24.05 | 0.1331 | 0 | 0 | 0 | 0 | 40 |
| split_6x10 | aggregate | 24 | −23.95 | 0 | 48 | 0 | 48 | 0 | 0 |
| split_6x10 | aggregate, throttle off | 24 | −23.95 | 0 | 48 | 0 | 48 | 0 | 0 |

The other split_3x20 rejections under aggregate and legacy are `resting_size_limit`
(its 20-lot order is above 2 × depth on some books). Paired effects relative to the
aggregate gate, large maker, filled lots: legacy −25.45 [−28.88, −22.20]; throttle
off +8.13 [6.48, 9.70] (95% bootstrap CIs, `summary.json`).

**Split probes:** turning off the throttle changes nothing. Their cancel ratio stays
at 0.50 or 0.67 under aggregate (0.86 at most under any gate), so the throttle never
fires. All blocking comes from the aggregate cap.

## Cancel-ratio history, large market maker

Seed 4000: cumulative subject (submits, cancels) at each decision minute.
`cancel_window_seconds` = 1800 is longer than the 720 s session, so the ratio is
cumulative and never decays.

| Minute | 4 | 5 | 6 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|
| legacy | 16, 11 | 20, 15 | 20, 19 (throttled at t = 304) | 20, 19 | 20, 19 | 20, 19 |
| aggregate | 8, 5 | 10, 7 | 12, 9 | 20, 17 | 20, 19 (throttled at t = 602) | 20, 19 |
| aggregate, throttle off | 8, 5 | 10, 7 | 12, 9 | 20, 17 | 22, 19 | 24, 21 |

Across the 40 seeds, legacy is throttle-armed at minute 6 in 37 seeds and at minute 12
in all 40. Aggregate is first armed at minute 11 (28 seeds) and at minute 12 in 36.
Per-seed, per-minute histories are in `traces.jsonl.gz` (`cancel_ratio_history`).

## Why aggregate retained 51.4 lots and legacy 24.0

It is an interaction between the size cap and the throttle's 20-submission minimum.
The aggregate gate is not less strict.

1. The large maker quotes 12 lots at two levels per side each minute, after first
   cancelling its previous quotes.
2. Under **legacy**, all four orders pass: 12 ≤ 2 × depth = 20 per order, and two
   levels are allowed. That is 4 submits per minute, so 20 submissions accumulate
   by minute 5. At minute 6 the maker cancels its 4 quotes, the ratio reaches
   19/20 = 0.95 > 0.9, and every new submission is refused from t ≈ 304 s on.
   Because the window covers the whole session and no new submits are accepted,
   the lockout is absorbing: 7 of 12 decision steps are dead.
3. Under **aggregate**, the second 12-lot level on each side would make same-side
   display 24 > 20, so the cap rejects it (about 24 cap rejections per episode).
   The maker submits 2 orders per minute and needs until minute 10 to reach 20
   submissions. It is throttled only at t ≈ 602 s and loses the last 2 steps.
4. Removing the throttle from the aggregate gate restores 59.05 lots, versus 59.48
   with no gate. The aggregate cap itself costs about 0.4 lots. The throttle costs
   about 8 lots under aggregate and about 34 under legacy.

So the 51.4 vs 24.0 gap in `review-v0.4` is a throttle-timing artefact. Halving the
submission rate delays the 20-submission arming point by about 5 minutes, and the
session-long cancel window makes any lockout permanent. A maker that quoted one
level from the start would show the same delay under legacy too. The throttle's
false-rejection cost on legitimate makers therefore depends on quoting cadence and
episode length, not on how strict the gate looks. The planned two-hour episode with
a 20-minute window (design doc) should be checked before this cost is reported.
