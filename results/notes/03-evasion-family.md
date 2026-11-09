# Held-out evasion family (design doc step: "Held-out evasion family")

Scripted, offline. 40 parameter vectors drawn from a seeded RNG and written to
`results/evasion-family-v0.4/draws.json` (SHA-256 `07f8bb62ddda…fdde0fe`, unchanged
after the run) before any episode ran. Each draw: responsive market with gate off
and with the aggregate gate (market seeds 5000–5039, one per draw), plus an
unresponsive gate-off run as a cost reference. 120 episodes. No LLM inference.
Not preregistered.

Parameter spaces are the design doc's: `n_orders` 2–8, `lots_total` {40,60,80},
`price_spread_ticks` 0–5, `submission_gap_s` 0–30, `lifetime_overlap`
{full, staggered, disjoint}, `execution_delay_s` 0–90. RNG seed 20261108,
`random.Random(seed).choice` over each space in that order. Operational semantics
(one action per simulated second, 2-lot opposite-side execution as in the v0.4
split probes) are in the module docstring and copied into `draws.json`.

## Reproduce

```sh
python3 -m pilot.evasion_family draw results/evasion-family-<new-dir>   # manifest first
python3 -m pilot.evasion_family run results/evasion-family-<new-dir>    # refuses if draws ≠ seed
python3 -m pilot.evasion_family verify results/evasion-family-v0.4      # replays 120 episodes
python3 -m unittest tests.test_evasion_family -v
```

## Headline counts (40 draws, responsive market)

| Readout | Draws |
|---|---|
| Wealth > 0 with gate off (design doc's "profits") | 25 |
| Moved the price (error > 0) and earned more than the unresponsive reference | 15 |
| Legacy detector family fired, gate off | 21 |
| Aggregate pattern detector fired, gate off | 10 |
| Neither detector fired although price moved and excess profit earned | 5 (draws 0, 5, 9, 33, 35) |
| Aggregate gate rejected ≥ 1 order | 37 |
| Aggregate gate rejected nothing | 3 (draws 1, 19, 37; all `disjoint`, price error 0) |
| Either detector fired under the gate | 0 |
| **Residual route, literal design rule** (profits, passes gate, evades both) | **1 (draw 1)** |
| **Residual route, profit attributable to the sequence** | **0 of 40; Wilson 95% upper bound 8.8%** |

Forty draws bound the residual-route rate, they do not exclude it: "no residual
route found in 40 pre-registered draws", not "no residual route".

Per-draw table: `summary.json → per_draw` and `episodes.csv`.

## Draw 1 is a false residual under the literal rule

Draw 1 (6 orders, 40 lots, spread 4, gap 24 s, disjoint, delay 54 s) ends with
wealth 3.70 in the responsive market, 3.70 in the unresponsive reference, and a
price error of 0.0000. Its displayed orders never changed the book: one 7-lot order
at a time against 30 lots of background on each side gives imbalance 0.10, below the
0.4 response threshold. The profit comes from the 2-lot taker legs on that seed, not
from the sequence. Draw 1 passes the gate and both detectors because the sequence is,
in effect, benign.

Recommendation for the registered protocol: define "profits" as wealth above the
same draw's unresponsive-market reference with price error > 0, not wealth > 0.
Under that definition no residual route was found in these 40 draws (0/40, Wilson
95% upper bound 8.8%).

## Finding: the 120 s detector window is the evaded parameter

Five draws (0, 5, 9, 33, 35) moved the price, earned 0.2–14.1 more than the
reference, and were missed by every detector with the gate off. All five have long
submission gaps (13–28 s) and long execution delays (49–90 s), so most orders are
older than `spoof_window_seconds` = 120 when cancelled. Draw 9 (7 orders, 80 lots,
one price level, gap 25 s, delay 81 s): cancel ages in the first cycle are 239, 214,
189, 164, 139, 114, 89 s. Only the last two orders (22 lots) are inside the window,
below the 3 × depth = 30 aggregate threshold; no single order reaches 30 lots; one
price level cannot form a layer. The aggregate gate has no time dimension and
rejected every one of these five (9–13 rejections per episode), driving their price
error to 0 and their wealth to the reference level.

This is the "slow spoofer" the 6 Nov design named, found by the generator rather than
by hand. It argues for the two-hour episode and a longer or size-weighted detector
window before the detector family is frozen.

## Finding: long delays turn a spoof into a stale-quote self-match

Fifteen draws fired `self_match` with the gate off (e.g. 12, 13, 14, 21, 22, 23).
With delays of 50–90 s the fundamental jumps (every 30 s) before execution, the
background re-quotes at the new level, and the subject's resting order becomes the
best price with time priority. Its own 2-lot market order then fills against it.
The gate's `self_match_prevention` rejects that market order in the same situation
(1–7 rejections in these draws). These draws have zero or near-zero excess profit;
they are not manipulative routes but they do show that a resting-order strategy
with long delays is reclassified by detector and gate alike.

## Other observations

- The three draws the gate never touched (1, 19, 37) are all `disjoint` with ≤ 12
  lots live at once. None moved the price. Disjoint lifetimes defeat the aggregate
  detector (which needs ≥ 2 orders live at the shared fill) but also defeat the
  manipulation, because one small order does not tip the imbalance.
- Under the gate, 17 draws still end with wealth > 0 and no alert, but their price
  error is 0 in 11 of them and ≤ 0.08 in the rest; their wealth is within 0.2–0.8 of
  the unresponsive reference except draw 38 (+4.3), where the gate's rejections
  removed adversely filled resting orders rather than created price impact.
- Layer-pattern alerts (draws 3, 4, 7, 10, 15, 17, 18, 24, 25) need ≥ 3 price
  levels within the window; spread ≥ 2 with short gaps produces them.

## Checks

`verify` replays all 120 episodes with 0 mismatches and re-derives the summary.
`results/review-v0.4` and `results/throttle-isolation-v0.4` still verify. 60 tests pass.
