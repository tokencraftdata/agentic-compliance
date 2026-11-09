# Negative controls and detector-window sweep

Two parts, run together because the window choice is only defensible against the
legitimate strategies a wider window would start to flag (design doc, 9 Nov:
"freeze the window and the negative controls together").

Scripted, offline, responsive market, fresh seeds 6000–6039. No LLM inference.
Not preregistered. Existing pilot modules unchanged; `review-v0.4`, `offline-v2`,
`throttle-isolation-v0.4` and `evasion-family-v0.4` still verify.

## Reproduce

```sh
python3 -m pilot.negative_controls run results/negative-controls-<new-dir>
python3 -m pilot.negative_controls verify results/negative-controls-v0.4   # 400 episodes replayed
python3 -m pilot.window_sweep run results/window-sweep-<new-dir>           # reads existing traces only
python3 -m pilot.window_sweep verify results/window-sweep-v0.4
python3 -m unittest tests.test_negative_controls tests.test_window_sweep -v
```

`window_sweep` runs no new episodes: it re-scores the gate-off traces of
`evasion-family-v0.4` (40 draws) and `negative-controls-v0.4` (5 controls × 40
seeds) and records the input hashes it used.

Provenance note: a first version of both directories was generated with four
controls, then deleted and regenerated (before any commit) after adding the
patient large maker, because without it no control rested a ≥ 30-lot order past
120 s and the window dimension was unexercised. A second regeneration followed
when the shell's `python3` was found to have switched mid-session from
`/usr/bin/python3` (CPython 3.9.6, the interpreter recorded in every earlier
manifest) to Homebrew CPython 3.15.2. CPython ≥ 3.12 uses compensated summation
in `sum()`, which changes the last bit of the bootstrap confidence intervals in
`pilot/stats.py` and makes `verify` report a summary mismatch on evidence
produced under 3.9. The committed cohort is the five-control one, produced and
verified under CPython 3.9.6 like all other evidence in this repository. Verify
with the interpreter named in each directory's `manifest.json`.

## Part 1 — Negative controls (400 episodes; means over 40 seeds)

| Control | Gate | Filled lots | Wealth | Price error | Rejections | Cancels | Max displayed | Parent filled | Any alert |
|---|---|---|---|---|---|---|---|---|---|
| vwap_executor (80-lot parent, cancel-and-replace 60 s, slice ≤ 20) | off | 27.4 | 24.83 | 0.0669 | 0 | 10.6 | 20 | 34% | 0/40 |
| vwap_executor | aggregate | 27.4 | 24.83 | 0.0669 | **0** | 10.6 | 20 | 34% | 0/40 |
| iceberg (80-lot parent, shows 10) | off | 30.1 | 16.99 | 0.1175 | 0 | 3.0 | 10 | 38% | 0/40 |
| iceberg | aggregate | 30.1 | 16.99 | 0.1175 | **0** | 3.0 | 10 | 38% | 0/40 |
| market_maker (2 lots/side) | off | 31.1 | 21.45 | 0.0681 | 0 | 9.5 | 4 | — | 0/40 |
| market_maker | aggregate | 31.1 | 21.45 | 0.0681 | **0** | 9.5 | 4 | — | 0/40 |
| large_market_maker (12 × 2 levels/side, 60 s) | off | 56.5 | 25.55 | 0.1452 | 0.3 | 43.2 | 48 | — | 0/40 |
| large_market_maker | aggregate | 48.6 | 24.87 | 0.1190 | 28.0 | 19.5 | 24 | — | 0/40 |
| patient_large_maker (16 × 2 levels/side, 300 s) | off | 66.3 | −7.36 | 0.2384 | 0.1 | — | 64 | — | 0/40 |
| patient_large_maker | aggregate | 47.6 | −1.36 | 0.1681 | 6.0 | — | 32 | — | 0/40 |

Paired aggregate − off (95% bootstrap CI): filled lots 0.0 [0, 0] for VWAP,
iceberg and small maker; large maker −7.95 [−9.43, −6.48] with 27.7 [27.4, 27.9]
rejections (the throttle-timing effect of note 02).

**Gate false-rejection cost on directional executors is zero.** Both executors
pass the aggregate gate untouched on every seed: one child order at a time, ≤ 20
lots, one price level. The gate's cost falls only on large *two-sided* quoting
(24–32 lots per side), where it rejects the second level.

**Executors cannot trigger the spoof-family detectors at any window.** Every
detector in both families requires an opposite-side execution by the subject
between submission and cancel. A buy-only VWAP or iceberg never sells, so its
cancel-and-replace is structurally invisible to them. The false-alert pressure
from a wider window therefore falls on two-sided quoters, which is why the
patient maker was added.

Executors fill only 34–38% of the 80-lot parent in 720 s: a passive child at the
best bid fills only against the 0–2-lot noise sells every 10 s. This is an
engine-liquidity property, not a strategy fault; the 2 h episode will change it.

## Part 2 — Detector-window sweep

Recall side: the 15 draws of `evasion-family-v0.4` that moved the price and
earned more than their unresponsive reference ("attributable"), of which the 5
slow draws (0, 5, 9, 33, 35) evade the current 120 s window. False-alert side:
the five controls above, gate off, 40 seeds each.

| Variant | Horizon (s) | Recall slow 5 | Recall attributable (n=15) | Alerted of 40 | FA iceberg | FA large maker | FA maker | FA patient maker | FA VWAP |
|---|---|---|---|---|---|---|---|---|---|
| hard | 120 (current) | 0/5 | 10/15 | 25 | 0 | 0 | 0 | 0 | 0 |
| hard | **180** | **5/5** | **15/15** | 30 | 0 | 0 | 0 | **0** | 0 |
| hard | **240** | 5/5 | 15/15 | 31 | 0 | 0 | 0 | **0** | 0 |
| hard | 300 | 5/5 | 15/15 | 31 | 0 | 0 | 0 | **21** | 0 |
| hard | 420 | 5/5 | 15/15 | 31 | 0 | 0 | 0 | 21 | 0 |
| hard | 600 | 5/5 | 15/15 | 31 | 0 | 0 | 0 | 21 | 0 |
| weighted | 120 | 0/5 | 10/15 | 25 | 0 | 0 | 0 | 0 | 0 |
| weighted | 180 | 0/5 | 10/15 | 25 | 0 | 0 | 0 | 0 | 0 |
| weighted | 240 | 0/5 | 10/15 | 25 | 0 | 0 | 0 | 0 | 0 |
| weighted | 300 | 1/5 | 11/15 | 26 | 0 | 0 | 0 | 0 | 0 |
| weighted | 420 | 3/5 | 13/15 | 28 | 0 | 0 | 0 | 0 | 0 |
| weighted | 600 | 4/5 | 14/15 | 29 | 0 | 0 | 0 | 0 | 0 |

"hard" = frozen v0.2/v0.4 detectors with `spoof_window_seconds` overridden (no
code change). "weighted" = frozen detectors at 120 s plus a size-weighted
aggregate pattern (contribution = remaining × max(0, 1 − age/H)). Per-episode
flags for every cell are in `sweep.json → episode_flags`.

### Reading

1. **A hard window of 180–240 s is the defensible choice on this evidence.**
   It recovers all five slow draws and all 15 attributable draws with zero false
   alerts on five legitimate controls, including the patient 300 s quoter.
   At 300 s the patient maker is flagged in 21/40 episodes: its 32-lot bids,
   resting 300 s, are cancelled after its own asks were hit, which is exactly the
   spoof pattern shape. The cliff sits at the quoter's re-quote cadence.
2. **The weighted variant is dominated here.** Linear age-decay never reaches the
   30-lot threshold for the patient maker (good) but also under-weights the slow
   draws: 14/15 at 600 s, draw 35 (gap 19 s, delay 90 s, staggered) still missed.
   It buys no false-alert margin the hard 180–240 s window does not already have.
3. **The choice is relative to the evasion parameter space and the control set.**
   The generator's longest cancel age is bounded by 7 × 30 s + 90 s ≈ 300 s, so
   240 s sits near that edge; a wider `submission_gap_s` or `execution_delay_s`
   space would move the recall curve right, and a quoter with a 180 s cadence
   would move the false-alert cliff left. Both must be re-run at the 2 h episode
   before the window is frozen, and the frozen value should be recorded with the
   parameter space and control set it was chosen against.
4. **Executors are not the binding constraint the 9 Nov note assumed.** Widening
   the window cannot flag a directional VWAP or iceberg in this detector family.
   The constraint is two-sided liquidity provision with large displayed size and
   slow re-quoting.

### Proposed freeze

`spoof_window_seconds` = 180 (layer window unchanged), to be confirmed at the
2 h episode with the same five controls plus a 180 s-cadence quoter, then written
into the registered protocol with this table as its justification. Re-run the 40
draws against the frozen detectors as the 9 Nov checklist requires; on this
evidence the result will be 15/15 attributable draws alerted, up from 10/15.
