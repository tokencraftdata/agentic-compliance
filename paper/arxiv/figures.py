"""Draw the paper's figures from the saved results, as vector PDFs.

Usage, from the repository root (needs matplotlib and numpy):
    python paper/arxiv/figures.py

Every number comes from files under results/; nothing is typed in by hand.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / 'figures'
TEXT_WIDTH = 6.5  # inches, the arxiv.sty text block

INK, INK2, MUTED, GRID = '#1f1f1e', '#52514e', '#8a8984', '#e4e3df'
BLUE, ORANGE, AQUA = '#2a78d6', '#eb6834', '#1baf7a'
BLUE_RAMP = ['#f3f7fd', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']
ORDINAL = ['#86b6ef', '#3987e5', '#1c5cab', '#0d366b']   # weakest to strictest control
SEQ = LinearSegmentedColormap.from_list('seq', BLUE_RAMP)
DIV = LinearSegmentedColormap.from_list('div', ['#1c5cab', '#86b6ef', '#f0efec', '#f0a39a', '#c0392b'])

plt.rcParams.update({
    'font.family': 'serif', 'font.serif': ['STIXGeneral', 'DejaVu Serif'], 'mathtext.fontset': 'stix',
    'font.size': 8.5, 'axes.titlesize': 9, 'axes.labelsize': 8.5, 'xtick.labelsize': 8, 'ytick.labelsize': 8,
    'legend.fontsize': 8, 'axes.edgecolor': INK2, 'axes.labelcolor': INK, 'text.color': INK,
    'xtick.color': INK2, 'ytick.color': INK2, 'axes.linewidth': 0.6, 'xtick.major.width': 0.6,
    'ytick.major.width': 0.6, 'xtick.major.size': 3, 'ytick.major.size': 3,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.titleweight': 'normal',
    'axes.titlelocation': 'left', 'axes.titlepad': 6, 'pdf.fonttype': 42, 'savefig.bbox': 'tight',
    'savefig.pad_inches': 0.02, 'figure.dpi': 150,
})


def load(path):
    return json.loads((ROOT / path).read_text())


def heat(ax, values, cmap, norm, fmt, dark):
    rows, cols = values.shape
    ax.imshow(values, cmap=cmap, norm=norm, aspect='auto')
    for i in range(rows):
        for j in range(cols):
            v = values[i, j]
            ax.text(j, i, fmt(v), ha='center', va='center', fontsize=7.5,
                    color='white' if dark(v) else INK)
    ax.set_xticks(np.arange(cols + 1) - .5, minor=True)
    ax.set_yticks(np.arange(rows + 1) - .5, minor=True)
    ax.grid(which='minor', color='white', linewidth=1.5)
    ax.tick_params(which='minor', length=0)
    ax.tick_params(which='major', length=0)
    for side in ax.spines.values():
        side.set_visible(False)


def cap_response():
    data = load('results/sensitivity-v1/summary.json')
    thresholds, caps = [0.1, 0.2, 0.25, 0.3, 0.4, 0.5], [1, 2, 3, 4, 6]

    def cell(**kw):
        return next(c for c in data['cells'] if all(c[k] == v for k, v in kw.items()))
    delta = np.zeros((len(caps), len(thresholds)))
    loss = np.zeros_like(delta)
    for i, cap in enumerate(caps):
        for j, t in enumerate(thresholds):
            rows = [r['response_wealth_difference'] for r in data['paired_differences']
                    if r['cohort'] == 'cap_response' and r['strategy'] == 'split_6x10'
                    and r['gate'] == 'aggregate' and r['cap'] == cap and r['threshold'] == t]
            delta[i, j] = np.mean(rows)
            off = cell(cohort='cap_response', strategy='large_market_maker', gate='off', threshold=t, aware=True)
            on = cell(cohort='cap_response', strategy='large_market_maker', gate='aggregate', threshold=t,
                      cap=cap, aware=True)
            loss[i, j] = 100 * (off['filled_lots'] - on['filled_lots']) / off['filled_lots']
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 2.55), gridspec_kw={'wspace': 0.32})
    ax = axes[0]
    heat(ax, delta, SEQ, plt.Normalize(0, 60), lambda v: f'{v:.0f}', lambda v: v > 30)
    # Proposition 2: activation iff m > 6t/(1-t). Draw the boundary on cell edges, as a staircase.
    sx, sy = [], []
    for j, t in enumerate(thresholds):
        first = next((i for i, m in enumerate(caps) if m > 6 * t / (1 - t)), len(caps))
        sx += [j - 0.5, j + 0.5]
        sy += [first - 0.5, first - 0.5]
    ax.plot(sx, sy, color=ORANGE, linewidth=1.8, solid_joinstyle='miter', clip_on=False)
    ax.set_title('Split probe: response-dependent gain $D$ (tick-lots)')
    ax.legend(handles=[plt.Line2D([], [], color=ORANGE, linewidth=1.8)],
              labels=[r'activation boundary $m = 6t/(1-t)$'], loc='upper center', bbox_to_anchor=(0.5, -0.2),
              frameon=False, handlelength=2.4, fontsize=7.5)
    ax2 = axes[1]
    heat(ax2, loss, DIV, TwoSlopeNorm(0, -100, 100), lambda v: f'{v:.0f}', lambda v: abs(v) > 60)
    ax2.set_title('Large maker: volume loss (% of ungated)')
    for a in axes:
        a.set_xticks(range(len(thresholds)), [str(t) for t in thresholds])
        a.set_yticks(range(len(caps)), [str(m) for m in caps])
        a.set_xlabel('Response threshold $t$')
        a.set_ylabel('Aggregate cap multiple $m$')
    fig.savefig(OUT / 'cap_response.pdf')
    plt.close(fig)


def throttle_horizon():
    data = load('results/sensitivity-v1/summary.json')

    def cell(**kw):
        return next(c for c in data['cells'] if all(c[k] == v for k, v in kw.items()))
    windows = [120, 300, 900, 1800]
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 2.35), sharey=True, gridspec_kw={'wspace': 0.08})
    for ax, gate in zip(axes, ['legacy', 'aggregate']):
        for horizon, color, label in [(720, ORANGE, '12-minute session'), (3600, BLUE, '60-minute session')]:
            off = cell(cohort='throttle_horizon', gate='off', horizon=horizon)['filled_lots']
            ys = [100 * cell(cohort='throttle_horizon', gate=gate, horizon=horizon, window=w, throttle=True)
                  ['filled_lots'] / off for w in windows]
            free = 100 * cell(cohort='throttle_horizon', gate=gate, horizon=horizon, throttle=False)['filled_lots'] / off
            ax.plot(range(4), ys, color=color, linewidth=1.8, marker='o', markersize=4.5,
                    markeredgecolor='white', markeredgewidth=0.8, zorder=3)
            ax.plot([4.6], [free], marker='D', color=color, markersize=4.5, markeredgecolor='white',
                    markeredgewidth=0.8, zorder=3)
            if gate == 'aggregate':
                ax.annotate(label, (3, ys[-1]), xytext=(4, 2 if horizon == 720 else -9),
                            textcoords='offset points', color=color, fontsize=8)
        ax.axhline(100, color=MUTED, linewidth=0.8, linestyle=(0, (1, 2)))
        ax.axvline(3.8, color=GRID, linewidth=0.8)
        ax.set_xticks([0, 1, 2, 3, 4.6], ['120', '300', '900', '1,800', 'off'])
        ax.set_xlim(-0.35, 5.0)
        ax.set_ylim(0, 112)
        ax.set_xlabel('Throttle window (s)')
        ax.set_title(f'{gate.capitalize()} gate')
        ax.grid(axis='y', color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
    axes[0].set_ylabel('Volume, % of ungated')
    axes[0].text(4.6, 103, 'throttle\noff', ha='center', va='bottom', fontsize=7, color=INK2)
    fig.savefig(OUT / 'throttle_horizon.pdf')
    plt.close(fig)


def cadence():
    data = load('results/paper-review-v1/summary.json')
    windows = [120, 180, 240, 300, 420, 600]
    rows = [c for c in data['cadence_cells'] if c['gate'] == 'off']
    values = np.array([[c['window_alerts'][str(w)] for w in windows] for c in rows])
    fig, ax = plt.subplots(figsize=(TEXT_WIDTH * 0.62, 2.2))
    heat(ax, values, SEQ, plt.Normalize(0, 40), lambda v: f'{v}/40', lambda v: v >= 24)
    ax.set_xticks(range(len(windows)), [str(w) for w in windows])
    ax.set_yticks(range(len(rows)), [str(r['cadence']) for r in rows])
    ax.set_xlabel('Detector window $W$ (s)')
    ax.set_ylabel('Maker re-quote interval (s)')
    # The age condition a <= W: alerts are possible only where W >= the re-quote interval.
    steps_x, steps_y = [-0.5], [-0.5]
    for i, r in enumerate(rows):
        j = next(k for k, w in enumerate(windows) if w >= r['cadence'])
        steps_x += [j - 0.5, j - 0.5]
        steps_y += [i - 0.5, i + 0.5]
    ax.plot(steps_x[1:], steps_y[1:], color=ORANGE, linewidth=1.6)
    ax.set_title('Conduct alerts on a legitimate maker (episodes of 40)')
    fig.savefig(OUT / 'cadence.pdf')
    plt.close(fig)


def calibration():
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_WIDTH, 2.45), gridspec_kw={'wspace': 0.28})
    stocks = [('AAPL', BLUE), ('INTC', ORANGE), ('MSFT', AQUA)]
    ax = axes[0]
    xs = np.linspace(-1, 1, 50)
    ax.plot(xs, xs, color=MUTED, linewidth=1.0, linestyle=(0, (4, 2)))
    ax.text(0.33, 0.55, r'stress, $\kappa=1$', color=INK2, fontsize=7.5, ha='right')
    slopes = {}
    for ticker, color in stocks:
        e = load(f'results/lobster-calibration-v1/{ticker}/calibration.json')['estimates']
        h = e['h1s']
        bins = h['i3_binned_mean_move']
        centers = [(b['bin'][0] + b['bin'][1]) / 2 for b in bins]
        ax.plot(centers, [b['mean_move_half_spreads'] for b in bins], color=color, marker='o', markersize=4.5,
                linewidth=1.6, markeredgecolor='white', markeredgewidth=0.8, zorder=3)
        slopes[ticker] = [e[k]['i3'] for k in ('h1s', 'h5s', 'h10s')]
        ax.annotate(ticker, (centers[-1], bins[-1]['mean_move_half_spreads']),
                    xytext=(6, {'AAPL': -9, 'INTC': 2, 'MSFT': -2}[ticker]),
                    textcoords='offset points', color=color, fontsize=8, va='center')
    ax.axhline(0, color=GRID, linewidth=0.8, zorder=0)
    ax.set_xlim(-1.05, 1.3)
    ax.set_ylim(-0.75, 1.25)
    ax.set_xlabel('Imbalance of top three levels, $I$')
    ax.set_ylabel('Next-second mid move (half-spreads)')
    ax.set_title('Mean one-second move by imbalance')
    ax2 = axes[1]
    horizons = [1, 5, 10]
    for k, (ticker, color) in enumerate(stocks):
        offs = (k - 1) * 0.16
        est = slopes[ticker]
        y = [s['slope_half_spreads'] for s in est]
        lo = [s['slope_half_spreads'] - s['slope_ci95_block_bootstrap'][0] for s in est]
        hi = [s['slope_ci95_block_bootstrap'][1] - s['slope_half_spreads'] for s in est]
        ax2.errorbar(np.array(range(3)) + offs, y, yerr=[lo, hi], fmt='o', color=color, markersize=4.5,
                     markeredgecolor='white', markeredgewidth=0.8, elinewidth=1.2, capsize=0, label=ticker)
    ax2.axhline(0, color=INK2, linewidth=0.6, zorder=0)
    for xv in (0.5, 1.5):
        ax2.axvline(xv, color=GRID, linewidth=0.8, zorder=0)
    ax2.set_xticks(range(3), [f'{h} s' for h in horizons])
    ax2.set_xlim(-0.5, 2.5)
    ax2.set_xlabel('Horizon')
    ax2.set_ylabel('Slope (half-spreads per unit $I$)')
    ax2.set_title('Fitted slope with 95% block-bootstrap interval')
    ax2.legend(handles=[plt.Line2D([], [], color=c, marker='o', linestyle='', markersize=4.5) for _, c in stocks],
               labels=[t for t, _ in stocks], frameon=False, loc='upper left', handletextpad=0.1)
    ax2.grid(axis='y', color=GRID, linewidth=0.6)
    ax2.set_axisbelow(True)
    fig.savefig(OUT / 'calibration.pdf')
    plt.close(fig)


def adversary():
    path = ROOT / 'results/adversary-v1/summary.json'
    if not path.exists():
        return
    rows = json.loads(path.read_text())['adversary']
    responses = [('linear_k1', r'Stress, $\kappa=1$'), ('fitted_msft', 'Fitted, MSFT'),
                 ('fitted_intc', 'Fitted, INTC'), ('fitted_aapl', 'Fitted, AAPL')]
    controls = [('none', 'No gate'), ('legacy', 'Legacy gate'), ('aggregate_2x', 'Aggregate cap, $m=2$'),
                ('aggregate_1x', 'Aggregate cap, $m=1$')]
    fig, ax = plt.subplots(figsize=(TEXT_WIDTH, 2.5))
    for k, ((ctrl, label), color) in enumerate(zip(controls, ORDINAL)):
        ys, means, lows, highs = [], [], [], []
        for i, (resp, _) in enumerate(responses):
            r = next(x for x in rows if x['control'] == ctrl and x['response'] == resp)['test']
            ys.append(i + (k - 1.5) * 0.17)
            means.append(r['mean_D'])
            lows.append(r['mean_D'] - r['D_ci95'][0])
            highs.append(r['D_ci95'][1] - r['mean_D'])
        ax.errorbar(means, ys, xerr=[lows, highs], fmt='o', color=color, markersize=4.5, elinewidth=1.3,
                    markeredgecolor='white', markeredgewidth=0.8, capsize=0, label=label)
    ax.axvline(0, color=INK2, linewidth=0.7)
    ax.set_yticks(range(len(responses)), [r[1] for r in responses])
    ax.invert_yaxis()
    ax.set_xlabel('Held-out mean gain $D$ of the best alert-free schedule found (tick-lots per session)')
    ax.grid(axis='x', color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis='y', length=0)
    ax.spines['left'].set_visible(False)
    ax.legend(frameon=False, loc='lower right', ncol=1, handletextpad=0.2)
    fig.savefig(OUT / 'adversary.pdf')
    plt.close(fig)


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    cap_response()
    throttle_horizon()
    cadence()
    calibration()
    adversary()
    print('figures written to', OUT)
