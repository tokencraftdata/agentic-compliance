# Two questions answered from existing traces

Read-only analysis of `results/offline-v2` (protocol 0.2, seeds 2000–2039) and
`results/review-v0.4` (exploratory 0.4, seeds 3000–3039). No new evidence directory.
Both directories replay-verify unchanged. Where a second-by-second mid path was
needed, the episode was re-simulated with the same deterministic engine the replay
check already uses (`pilot.revision.review_episode`, `pilot.run.episode`).

## Reproduce

```sh
python3 - <<'EOF'
import json, statistics as st
from pilot.revision import review_episode
cfg = json.load(open('config.json'))
for s in ('scripted_spoofer', 'split_3x20', 'split_6x10'):
    m = review_episode(cfg, 3000, s, True, 'off')
    err = [abs(x['mid'] - x['fundamental']) for x in m.samples]
    print(s, round(st.mean(err), 4), 'shifted seconds:', sum(e > 0 for e in err),
          'cycle 1:', [x['time'] for x in m.samples if x['time'] < 60 and abs(x['mid'] - x['fundamental']) > 0])
EOF
```

```sh
python3 - <<'EOF'
import csv, statistics as st
def w(path, **f):
    return [float(r['wealth_fundamental']) for r in csv.DictReader(open(path)) if all(r[k] == v for k, v in f.items())]
a = w('results/offline-v2/episodes.csv', strategy='scripted_spoofer', book_aware='True', control='False')
b = w('results/review-v0.4/episodes.csv', strategy='scripted_spoofer', book_aware='True', gate='off')
c = w('results/review-v0.4/episodes.csv', strategy='split_3x20', book_aware='True', gate='off')
print(st.mean(a), st.mean(b), st.mean(c), 'spoofer==3x20 per seed in v0.4:', sorted(b) == sorted(c))
EOF
```

## Q1. Why 6×10 gives price error 0.1331 and 3×20 / 1×60 give 0.0666

Price error is a **duration** measure of a binary shift, not a size measure.

The responsive background shifts its quotes by exactly `book_response_ticks` = 2
when the book imbalance exceeds 0.4. Background depth is 10 lots at 3 levels per
side (30 per side), so with D subject lots displayed on one side the imbalance is
D / (60 + D), which exceeds 0.4 only when **D ≥ 50**. While shifted, the mid sits
2 ticks from the fundamental; otherwise the error is 0. Mean error = 2 × (shifted
seconds) / 721 samples.

Each subject action takes one simulated second. Seed 3000, first cycle, gate off:

| Strategy | Displayed lots by second | Shifted seconds | Per episode (12 cycles) | Mean error |
|---|---|---|---|---|
| 1×60 | 60 at t=1; market t=1; cancel t=2 | t = 1, 2 → **2 s** | 24 s | 2×24/721 = **0.0666** |
| 3×20 | 20, 40, 60 at t=1–3; market t=3; cancels t=4–6 | t = 3, 4 → **2 s** | 24 s | **0.0666** |
| 6×10 | 10, 20, 30, 40, **50**, 60 at t=1–6; market t=6; cancels t=7–12 | t = 5, 6, 7, 8 → **4 s** | 48 s | 2×48/721 = **0.1331** |

1×60 and 3×20 cross the 50-lot threshold only at full size, so the book is shifted
for the one second before the market order and the one second after it, then the
first cancel drops D to 0 or 40 and the shift ends. 6×10 crosses at the fifth
10-lot order on the way up (D = 50, one second before the sixth) and stays above
the threshold after the first cancel on the way down (D = 50), so the shifted
state lasts two seconds longer per cycle. Same 2-tick displacement, twice the time.

Consequence for the paper: the "price error double the original spoofer" line in
the v0.4 text should read as "the book is displaced for twice as many seconds", not
as a larger displacement. The executed 2 lots are filled while shifted in all three
cases, which is why their wealth is identical (next question).

## Q2. Why 3×20 wealth is 22.05 and the original spoofer's is 24.25

They are different seed cohorts, not different strategies.

- `review-v0.4`, responsive market, gate off, seeds 3000–3039: scripted_spoofer
  22.05, split_3x20 22.05, split_6x10 22.05. The per-seed wealth vectors are
  **identical** across the three strategies.
- `offline-v2`, responsive market, control off, seeds 2000–2039: scripted_spoofer
  **24.25**. This is the figure in the design doc ("24.25 → −23.75").

Decomposition of wealth at fundamental, per seed, both cohorts:

| Component | Seeds 2000–2039 | Seeds 3000–3039 |
|---|---|---|
| Execution edge vs fundamental at fill (12 cycles × 2 lots × 2 ticks) | 24.00 on every seed | 24.00 on every seed |
| Fees (24 lots × 0.05) | −1.20 | −1.20 |
| Inventory carry (fundamental jumps while holding −2 lots between cycles) | **+1.45** | **−0.75** |
| Wealth | 24.25 | 22.05 |

The strategy's edge is deterministic: every cycle sells 2 lots at f+1 into a book
shifted up (or buys at f−1 into one shifted down), 2 ticks of edge each. The only
random term is the mark-to-fundamental of the −2-lot position it carries for 60 s
after each buy-side cycle, while the fundamental jumps by {−1, 0, 0, +1} every 30 s.
Cohort means +1.45 and −0.75 differ by 2.2 with SE 1.10 over 40 vs 40 unpaired seeds
(per-seed SD ≈ 5, range 14.8–34.8). It is seed-path noise.

Consequence for the paper: compare strategies only within a seed cohort (as the
paired within-seed design intends). The 22.05 vs 24.25 juxtaposition should be
removed or footnoted as a cross-cohort comparison with no strategy content.
