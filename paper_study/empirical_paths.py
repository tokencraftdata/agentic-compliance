"""Stylized facts of the simulator against LOBSTER data, and the adversary on real price paths.

Two checks, fixed in one plan before running:

1. Stylized facts. The same estimators are applied to 720-second mid-price
   sessions from the simulator (seeds 9100-9199, no subject orders) and to
   consecutive 740-second windows of the five LOBSTER samples (first and last
   ten minutes trimmed). Prices are in half-spread units: simulator ticks, and
   for the data the mid-price divided by that day's median half-spread. Reported:
   share of unchanged 10 s mid-prices, standard deviation and excess kurtosis of
   60 s returns, lag-1 autocorrelation of 10 s returns, and the mean
   autocorrelation of absolute 10 s returns at lags 1-5 (volatility clustering),
   with 95% bootstrap intervals over sessions.
2. Real price paths. The simulator's fundamental, a 30-second +-1-tick random
   walk, is replaced by a real mid-price path: window k of a LOBSTER sample, in
   half-spread units rounded to whole ticks, uses noise and response seed 9100+k.
   The adversary policies selected in results/adversary-v1 are replayed unchanged
   on these paths with the response fitted to the same stock, and the paired gain
   D is reported with bootstrap intervals over windows.

The LOBSTER files are not redistributed; their SHA-256 hashes are recorded and
`run` and `verify` need them:

    python -m paper_study.empirical_paths plan results/empirical-paths-v1
    python -m paper_study.empirical_paths run results/empirical-paths-v1 --lobster DIR [--workers 4]
    python -m paper_study.empirical_paths verify results/empirical-paths-v1 --lobster DIR

DIR holds the unzipped samples, either flat or in one folder per ticker.
"""
import argparse
import gzip
import hashlib
import json
import platform
import random
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pilot.evasion_family import schedule  # noqa: F401  (hashed source the replay depends on)
from pilot.run import canonical
from paper_study import adversary as A
from paper_study import calibration as C

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'config.json'
TICKERS = ['AAPL', 'AMZN', 'GOOG', 'INTC', 'MSFT']
SPEC = {
    'status': 'Exploratory; estimators, windows, cells and analysis fixed in this plan before running',
    'tickers': TICKERS,
    'session_open': 34200, 'session_close': 57600, 'trim_seconds': 600,
    'window_seconds': 740,           # session_seconds + 20, the length of the simulator's path
    'session_seconds': 720,
    'seed_base': 9100,
    'synthetic_seeds': list(range(9100, 9200)),
    'short_horizon_s': 10, 'long_horizon_s': 60, 'clustering_lags': [1, 2, 3, 4, 5],
    'replay_cells': [
        {'ticker': 'MSFT', 'response': 'fitted_msft'},
        {'ticker': 'INTC', 'response': 'fitted_intc'},
        {'ticker': 'MSFT', 'response': 'linear_k1'},
    ],
    'controls': ['none', 'aggregate_2x', 'aggregate_1x'],
    'policy_source': 'results/adversary-v1/summary.json',
    'bootstrap_resamples': 2000,
    'bootstrap_seed': 20261113,
}


# ---------------------------------------------------------------- data

def lobster_files(folder, ticker):
    folder = Path(folder)
    found = sorted(folder.glob(f'{ticker}_*_message_10.csv')) + sorted(folder.glob(f'{ticker}/{ticker}_*_message_10.csv'))
    if len(found) != 1:
        raise FileNotFoundError(f'expected one {ticker} message file under {folder}')
    message = found[0]
    return message, Path(str(message).replace('_message_', '_orderbook_'))


def day_series(folder, ticker, spec):
    """Mid-price in half-spread units on a one-second grid over the trimmed session."""
    message, book = lobster_files(folder, ticker)
    grid = C.snapshots(message, book)
    start = spec['session_open'] + spec['trim_seconds']
    stop = spec['session_close'] - spec['trim_seconds']
    mids, halves, last = [], [], None
    for t in range(start, stop + 1):
        if t in grid:
            last = grid[t]
        if last is None:
            raise ValueError('no book state at the start of the trimmed session')
        mids.append(last[0])
        halves.append(last[1])
    half = statistics.median(halves)
    return [m / half for m in mids], half, {'message': C.sha256(message), 'orderbook': C.sha256(book)}


def windows(series, spec):
    n = (len(series) - 1) // spec['window_seconds']
    w = spec['window_seconds']
    return [series[k * w:(k + 1) * w] for k in range(n)]


def tick_path(window, base):
    """Real mid-price window as a whole-tick fundamental path starting at the base price."""
    return [base + round(x - window[0]) for x in window]


# ---------------------------------------------------------------- stylized facts

def returns(session, h, length):
    return [session[t + h] - session[t] for t in range(0, length - h + 1, h)]


def corr(pairs):
    if len(pairs) < 3:
        return None
    xs, ys = [a for a, _ in pairs], [b for _, b in pairs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx > 0 and syy > 0 else None


def facts(sessions, spec):
    L, hs, hl = spec['session_seconds'], spec['short_horizon_s'], spec['long_horizon_s']
    short = [returns(s, hs, L) for s in sessions]
    long_ = [x for s in sessions for x in returns(s, hl, L)]
    flat = [x for r in short for x in r]
    m = statistics.mean(long_)
    var = statistics.mean([(x - m) ** 2 for x in long_])
    kurt = statistics.mean([(x - m) ** 4 for x in long_]) / var ** 2 - 3 if var > 0 else None
    lag1 = corr([(r[i], r[i + 1]) for r in short for i in range(len(r) - 1)])
    clusters = [corr([(abs(r[i]), abs(r[i + k])) for r in short for i in range(len(r) - k)])
                for k in spec['clustering_lags']]
    clusters = [c for c in clusters if c is not None]
    return {'zero_share_10s': sum(x == 0 for x in flat) / len(flat),
            'sd_60s': var ** 0.5,
            'excess_kurtosis_60s': kurt,
            'acf1_10s': lag1,
            'abs_acf_10s_lags1_5': statistics.mean(clusters) if clusters else None}


def facts_with_ci(sessions, spec, label):
    point = facts(sessions, spec)
    rng = random.Random(f"{spec['bootstrap_seed']}|facts|{label}")
    draws = {k: [] for k in point}
    for _ in range(spec['bootstrap_resamples']):
        sample = [sessions[rng.randrange(len(sessions))] for _ in sessions]
        for k, v in facts(sample, spec).items():
            if v is not None:
                draws[k].append(v)
    out = {'sessions': len(sessions)}
    for k, v in point.items():
        d = sorted(draws[k])
        out[k] = None if v is None else round(v, 6)
        out[k + '_ci95'] = [round(d[int(0.025 * len(d))], 6), round(d[int(0.975 * len(d)) - 1], 6)] if d else None
    return out


def synthetic_session(base, seed):
    m = A.ResponseMarket({**base, 'response_mode': 'step', 'response_threshold': 0.4}, seed, False, 'off')
    m.advance(base['session_seconds'])
    return [s['mid'] for s in m.samples]


# ---------------------------------------------------------------- replay

class PathMarket(A.ResponseMarket):
    """ResponseMarket whose fundamental follows a given path; noise and response streams are unchanged."""

    def __init__(self, config, seed, aware, gate, path):
        self._fundamental = path
        super().__init__(config, seed, aware, gate)
        if self.time != 0 or path[0] != self.path[0][0]:
            raise ValueError('the replayed path must start at the base price')
        self.path = [(f, side, qty) for f, (_, side, qty) in zip(path, self.path)]


def episode(config, seed, gate, aware, params, path):
    m = A.run_schedule(PathMarket(config, seed, aware, gate, path), params, config)
    alerts = A.extended_detect(A.subject_events(m.events), {**config, 'cancel_min_submissions': 20})
    metrics = m.metrics()
    return {'wealth': metrics['wealth_fundamental'], 'filled_lots': metrics['filled_lots'],
            'rejected': metrics['rejected_actions'],
            'alert': any(a['detector'] != 'excessive_cancels' for a in alerts),
            'activations': sum(s != 0 for _, _, s in m.response_inputs),
            'event_sha256': A.digest(m.events)}


def paired(config, seed, gate, params, path):
    on = episode(config, seed, gate, True, params, path)
    off = episode(config, seed, gate, False, params, path)
    return {'seed': seed, 'D': round(on['wealth'] - off['wealth'], 6), 'alert': on['alert'],
            'filled_lots': on['filled_lots'], 'rejected': on['rejected'],
            'activations': on['activations'], 'responsive_sha256': on['event_sha256'],
            'unresponsive_sha256': off['event_sha256']}


def replay_jobs(base, spec, paths, policies):
    jobs = []
    for cell in spec['replay_cells']:
        for c in spec['controls']:
            control, response = A.lookup(A.CONTROLS, c), A.lookup(A.RESPONSES, cell['response'])
            cfg = A.config_for(base, control, response, True)
            params = policies[(c, cell['response'])]
            for k, path in enumerate(paths[cell['ticker']]):
                jobs.append((cell['ticker'], cell['response'], c, cfg, spec['seed_base'] + k,
                             control['gate'], params, path))
    return jobs


def _replay_job(job):
    ticker, response, control, cfg, seed, gate, params, path = job
    return {'ticker': ticker, 'response': response, 'control': control,
            **paired(cfg, seed, gate, params, path)}


def summarize_replay(rows, spec, policies, synthetic):
    out = []
    for cell in spec['replay_cells']:
        for c in spec['controls']:
            sel = [r for r in rows if (r['ticker'], r['response'], r['control']) == (cell['ticker'], cell['response'], c)]
            d = [r['D'] for r in sel]
            label = f"replay|{cell['ticker']}|{cell['response']}|{c}"
            out.append({'ticker': cell['ticker'], 'response': cell['response'], 'control': c,
                        'params': policies[(c, cell['response'])], 'windows': len(sel),
                        'mean_D': round(statistics.mean(d), 6),
                        'D_ci95': A.bootstrap(d, spec['bootstrap_resamples'], f"{spec['bootstrap_seed']}|{label}"),
                        'positive_D': sum(x > 0 for x in d), 'alerts': sum(r['alert'] for r in sel),
                        'mean_filled_lots': round(statistics.mean(r['filled_lots'] for r in sel), 6),
                        'synthetic_mean_D': synthetic[(c, cell['response'])]['mean_D'],
                        'synthetic_D_ci95': synthetic[(c, cell['response'])]['D_ci95']})
    return out


# ---------------------------------------------------------------- plan / run / verify

def source_hashes():
    paths = [Path(__file__), Path(A.__file__), Path(C.__file__), Path(A.__file__).with_name('sensitivity.py'),
             BASE] + sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def load_policies(spec):
    s = json.loads((ROOT / spec['policy_source']).read_text())
    policies = {(a['control'], a['response']): a['best_params'] for a in s['adversary']}
    synthetic = {(a['control'], a['response']): a['test'] for a in s['adversary']}
    return policies, synthetic


def compute(p, lobster, workers):
    base, spec = p['config'], p['spec']
    series, halves, hashes = {}, {}, {}
    for t in spec['tickers']:
        series[t], halves[t], hashes[t] = day_series(lobster, t, spec)
    sessions = {t: [w[:spec['session_seconds'] + 1] for w in windows(series[t], spec)] for t in spec['tickers']}
    sim = [synthetic_session(base, s)[:spec['session_seconds'] + 1] for s in spec['synthetic_seeds']]
    paths = {t: [tick_path(w, base['base_price']) for w in windows(series[t], spec)] for t in spec['tickers']}
    replayed = [[x - base['base_price'] for x in path][:spec['session_seconds'] + 1] for path in paths['MSFT']]
    stylized = [{'source': 'simulator', **facts_with_ci(sim, spec, 'simulator')},
                {'source': 'simulator, MSFT path', **facts_with_ci(replayed, spec, 'replayed_msft')}] + \
               [{'source': t, 'median_half_spread_dollars': round(halves[t] / 10000, 6),
                 **facts_with_ci(sessions[t], spec, t)} for t in spec['tickers']]
    policies, synthetic = load_policies(spec)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_replay_job, replay_jobs(base, spec, paths, policies), chunksize=4))
    return stylized, rows, summarize_replay(rows, spec, policies, synthetic), hashes


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'replay_cells': len(SPEC['replay_cells']) * len(SPEC['controls'])}))


def run(out, lobster, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    stylized, rows, replay, hashes = compute(p, lobster, workers)
    with (out / 'episodes.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for r in rows:
            stream.write((canonical(r) + '\n').encode())
    summary = {'stylized_facts': stylized, 'replay': replay, 'lobster_sha256': hashes, 'llm_episodes': 0,
               'inference_scope': 'Bootstrap 95% intervals over sessions (facts) or 30 real-path windows (replay) '
                                  'for fixed policies selected on synthetic training paths.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'facts': [{k: s[k] for k in ('source', 'excess_kurtosis_60s', 'abs_acf_10s_lags1_5')}
                                for s in stylized],
                      'replay': [{k: r[k] for k in ('ticker', 'response', 'control', 'mean_D', 'D_ci95',
                                                    'synthetic_mean_D', 'alerts')} for r in replay]}, indent=1))


def verify(out, lobster, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    stylized, rows, replay, hashes = compute(p, lobster, workers)
    saved = json.loads((out / 'summary.json').read_text())
    assert hashes == saved['lobster_sha256'], 'LOBSTER files differ from the recorded ones'
    assert json.loads(json.dumps(stylized)) == saved['stylized_facts']
    assert json.loads(json.dumps(replay)) == saved['replay']
    with gzip.open(out / 'episodes.jsonl.gz', 'rt') as stream:
        assert [json.loads(line) for line in stream] == json.loads(json.dumps(rows))
    result = {'replay_episodes_rerun': len(rows), 'fact_sources': len(stylized),
              'summary_reproduced': True, 'source_and_evidence_hashes_verified': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--lobster', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.command != 'plan' and args.lobster is None:
        parser.error('--lobster is required for run and verify')
    {'plan': lambda: plan(args.output), 'run': lambda: run(args.output, args.lobster, args.workers),
     'verify': lambda: verify(args.output, args.lobster, args.workers)}[args.command]()
