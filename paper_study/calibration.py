"""Estimate the imbalance-to-price response from a LOBSTER order-book file (stdlib only).

The simulator's linear response moves background quotes in the direction of the
book imbalance I, with expected displacement kappa * I in units of the
simulator's half-spread (one tick). This module estimates the same quantity on
real data: on a one-second grid, the imbalance over the top three levels
(matching the simulator's three background levels) and the subsequent change
in the mid-price, measured in units of the prevailing half-spread.

    python -m paper_study.calibration <message.csv> <orderbook.csv> results/lobster-calibration-v1

The LOBSTER files are not redistributed; their SHA-256 hashes are recorded.
The estimate is predictive (imbalance forecasts the next move), not a causal
response to orders placed by one trader; see the paper for this caveat.
"""
import argparse
import csv
import hashlib
import json
import platform
import random
import statistics
from pathlib import Path

LEVELS = 3
HORIZONS = [1, 5, 10]
TRIM_SECONDS = 600          # skip the first and last ten minutes of the session
BLOCK_SECONDS = 300         # five-minute blocks for the block bootstrap
RESAMPLES = 2000
BOOT_SEED = 20261111
BINS = [-1.0, -0.6, -0.2, 0.2, 0.6, 1.0001]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def snapshots(message_path, book_path):
    """Book state at the last event at or before each whole second."""
    grid = {}
    with open(message_path) as m, open(book_path) as b:
        for msg, row in zip(csv.reader(m), csv.reader(b)):
            t = float(msg[0])
            if msg[1] == '7':
                continue
            v = list(map(int, row))
            ask, bid = v[0], v[2]
            if ask <= 0 or bid <= 0 or ask >= 9999999999 or bid <= -9999999999 or ask <= bid:
                continue
            asks = sum(v[4 * k + 1] for k in range(LEVELS))
            bids = sum(v[4 * k + 3] for k in range(LEVELS))
            grid[int(t) + 1] = ((ask + bid) / 2, (ask - bid) / 2, (bids - asks) / (bids + asks),
                                (v[3] - v[1]) / (v[3] + v[1]))
    return grid


def ols(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    through_origin = sum(x * y for x, y in zip(xs, ys)) / sum(x * x for x in xs)
    return slope, my - slope * mx, through_origin


def block_bootstrap(rows, key, seed):
    blocks = {}
    for r in rows:
        blocks.setdefault(r['t'] // BLOCK_SECONDS, []).append(r)
    blocks = list(blocks.values())
    rng = random.Random(seed)
    slopes = []
    for _ in range(RESAMPLES):
        sample = [r for _ in blocks for r in blocks[rng.randrange(len(blocks))]]
        slopes.append(ols([r[key] for r in sample], [r['y'] for r in sample])[0])
    slopes.sort()
    return [round(slopes[int(0.025 * RESAMPLES)], 6), round(slopes[int(0.975 * RESAMPLES) - 1], 6)]


def estimate(grid):
    times = sorted(grid)
    start, end = times[0] + TRIM_SECONDS, times[-1] - TRIM_SECONDS
    out = {}
    for h in HORIZONS:
        rows = []
        for t in range(start, end - h + 1):
            if t not in grid or t + h not in grid:
                continue
            mid, half, i3, i1 = grid[t]
            rows.append({'t': t, 'i3': i3, 'i1': i1, 'y': (grid[t + h][0] - mid) / half,
                         'half_ticks': half / 100})
        result = {'n_seconds': len(rows)}
        for key in ('i3', 'i1'):
            slope, intercept, origin = ols([r[key] for r in rows], [r['y'] for r in rows])
            result[key] = {'slope_half_spreads': round(slope, 6), 'intercept': round(intercept, 6),
                           'slope_through_origin': round(origin, 6),
                           'slope_ci95_block_bootstrap': block_bootstrap(rows, key, f'{BOOT_SEED}|{h}|{key}')}
        result['i3_binned_mean_move'] = []
        for lo, hi in zip(BINS, BINS[1:]):
            ys = [r['y'] for r in rows if lo <= r['i3'] < hi]
            if ys:
                result['i3_binned_mean_move'].append({'bin': [lo, round(min(hi, 1.0), 2)], 'n': len(ys),
                                                     'mean_move_half_spreads': round(statistics.mean(ys), 6)})
        out[f'h{h}s'] = result
    halves = [grid[t][1] / 100 for t in range(start, end) if t in grid]
    out['median_half_spread_ticks'] = statistics.median(halves)
    out['mean_abs_i3'] = round(statistics.mean(abs(grid[t][2]) for t in range(start, end) if t in grid), 6)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('message', type=Path)
    parser.add_argument('orderbook', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    grid = snapshots(args.message, args.orderbook)
    result = {'source_files': {args.message.name: sha256(args.message), args.orderbook.name: sha256(args.orderbook)},
              'method': {'levels': LEVELS, 'grid_seconds': 1, 'trim_seconds': TRIM_SECONDS,
                         'units': 'mid-price change divided by the half-spread at the start of the interval',
                         'bootstrap': f'{RESAMPLES} resamples of {BLOCK_SECONDS}-second blocks'},
              'estimates': estimate(grid),
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'python': platform.python_version()}
    (args.output / 'calibration.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['estimates'], indent=1))


if __name__ == '__main__':
    main()
