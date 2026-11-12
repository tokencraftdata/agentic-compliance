"""Robustness of the adversary results to the response window, the unit mapping and search restarts.

Three checks, fixed in one plan before running, reusing paper_study.adversary:

1. Response window. In paper_study.adversary the imbalance that drives the
   linear response counts every resting order, however far from the touch.
   The LOBSTER fits use the top three price levels only. Here the imbalance can
   instead count only orders within `response_window_ticks` of the best price on
   their side (3 ticks matches the fitted top three levels), so depth outside
   the fitted window has no effect on the price.
2. Unit mapping. The fitted scales map one simulator half-spread (one tick) to
   one real half-spread. The tick mapping instead maps one simulator tick to one
   real tick: scale = slope in half-spreads x median half-spread in ticks. Under
   it the five LOBSTER stocks span 0.13 (AAPL) to 0.24 (MSFT).
3. Search restarts. Each cell is searched with three independent search seeds
   at the original budget; the reported policy is the restart with the best
   training score, and the spread across restarts is reported.

It also reports, for each selected policy, the gain from displaying: wealth in
the responsive market minus wealth from the same execution schedule with no
displayed orders. And it reports the paired gain D of the five legitimate
scripts, to show whether D separates manipulation from liquidity provision.

    python -m paper_study.robustness plan results/robustness-v1
    python -m paper_study.robustness run results/robustness-v1 [--workers 4]
    python -m paper_study.robustness verify results/robustness-v1
"""
import argparse
import gzip
import hashlib
import json
import platform
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pilot.evasion_family import schedule
from pilot.negative_controls import control_actions
from pilot.run import canonical
from paper_study import adversary as A
from paper_study.sensitivity import BASE, ROOT

TICK_RESPONSES = [  # slope (half-spreads, 1 s, top three levels) x median half-spread (ticks)
    {'name': 'ticks_aapl', 'mode': 'linear', 'kappa': 1, 'scale': round(0.017851 * 7.5, 6)},
    {'name': 'ticks_msft', 'mode': 'linear', 'kappa': 1, 'scale': round(0.478911 * 0.5, 6)},
]
RESPONSES = A.RESPONSES + TICK_RESPONSES
CONTROLS = ['none', 'aggregate_2x', 'aggregate_1x']
SPEC = {
    'status': 'Robustness check; windows, responses, restarts and seeds fixed in this plan before running',
    'space': A.SPACE,
    'throttle': True,
    'train_seeds': list(range(9000, 9010)),
    'test_seeds': list(range(9100, 9200)),
    'random_candidates': 40,
    'local_steps': 40,
    'alert_penalty': 100.0,
    'bootstrap_resamples': 10000,
    'bootstrap_seed': 20261120,
    'search_seeds': [20261111, 20261121, 20261131],
    'groups': [
        {'name': 'window3', 'window': 3, 'responses': ['linear_k1', 'fitted_intc', 'fitted_msft']},
        {'name': 'window3_ticks', 'window': 3, 'responses': ['ticks_aapl', 'ticks_msft']},
        {'name': 'unlimited_restarts', 'window': None, 'responses': ['linear_k1', 'fitted_msft']},
    ],
    'controls': CONTROLS,
    'legit_strategies': ['vwap_executor', 'iceberg', 'market_maker', 'large_market_maker', 'patient_large_maker'],
    'legit_responses': ['linear_k1', 'fitted_msft'],
    'legit_windows': [None, 3],
    'legit_seeds': list(range(9300, 9400)),
    'notes': [
        'The first search seed equals adversary-v1, so unlimited_restarts restart 0 reproduces its searches.',
        'Display gain: responsive-market wealth of the schedule minus that of the same schedule without its '
        'limit orders and cancellations (market orders only), same gate, seed and path.',
        'Legitimate D uses the ungated market and the scripts of pilot.negative_controls.',
    ],
}


class WindowMarket(A.ResponseMarket):
    """ResponseMarket whose imbalance can be limited to orders near the best price on each side."""

    def windowed(self, side, window):
        orders = self.open_orders(side=side)
        if not orders:
            return 0
        if side == 'buy':
            best = max(o.price for o in orders)
            return sum(o.remaining for o in orders if o.price >= best - (window - 1))
        best = min(o.price for o in orders)
        return sum(o.remaining for o in orders if o.price <= best + (window - 1))

    def background(self):
        window = self.config.get('response_window_ticks')
        if window is None or self.config.get('response_mode', 'step') == 'step':
            return super().background()
        f, noise_side, noise_qty = self.path[self.time]
        self.fundamental = f
        bids, asks = self.windowed('buy', window), self.windowed('sell', window)
        imbalance = (bids - asks) / max(1, bids + asks)
        probability = min(1.0, self.config.get('response_scale', 1.0) * abs(imbalance))
        step = self.config['response_kappa'] if self.uniforms[self.time] < probability else 0
        shift = (step if imbalance > 0 else -step) if self.book_aware else 0
        self.response_inputs.append((self.time, imbalance, shift))
        for o in list(self.open_orders('liquidity')):
            self.cancel(o.id, 'liquidity')
        for level in range(1, 4):
            self.submit('liquidity', 'buy', self.config['background_depth'], f + shift - level)
            self.submit('liquidity', 'sell', self.config['background_depth'], f + shift + level)
        if self.time % 10 == 0 and noise_qty:
            self.submit('noise', noise_side, noise_qty)
        mid = (self.touch('buy') + self.touch('sell')) / 2
        self.samples.append({'time': self.time, 'mid': mid, 'fundamental': f,
                             'inventory': self.positions.get('subject', 0),
                             'wealth_at_fundamental': round(self.cash.get('subject', 0) +
                                                            self.positions.get('subject', 0) * f, 6)})


# paper_study.adversary builds its markets through this name; with no window set the
# subclass behaves exactly as the original, so earlier evidence is unaffected.
A.ResponseMarket = WindowMarket


def config(base, control, response, throttle, window):
    cfg = A.config_for(base, A.lookup(A.CONTROLS, control), A.lookup(RESPONSES, response), throttle)
    if window is not None:
        cfg['response_window_ticks'] = window
    return cfg


def run_execution_only(m, params, cfg):
    """A.run_schedule with the displayed orders removed: same cycles, same market orders."""
    events, duration = schedule(params)
    t0, cycle = 0, 0
    while t0 + duration <= cfg['session_seconds']:
        side = 'buy' if cycle % 2 == 0 else 'sell'
        m.advance(t0)
        for rel, action in events:
            m.advance(t0 + rel)
            if action[0] == 'market':
                m.act({'type': 'market', 'side': 'sell' if side == 'buy' else 'buy', 'qty': 2})
        cycle += 1
        t0 = -(-m.time // cfg['decision_interval']) * cfg['decision_interval']
    m.advance(cfg['session_seconds'])
    return m


def display_row(cfg, seed, gate, params):
    full = A.run_schedule(WindowMarket(cfg, seed, True, gate), params, cfg)
    bare = run_execution_only(WindowMarket(cfg, seed, True, gate), params, cfg)
    w_full, w_bare = full.metrics()['wealth_fundamental'], bare.metrics()['wealth_fundamental']
    return {'seed': seed, 'wealth': w_full, 'wealth_no_display': w_bare, 'display_gain': round(w_full - w_bare, 6)}


def cells(spec):
    return [(g['name'], g['window'], r, c, s) for g in spec['groups'] for r in g['responses']
            for c in spec['controls'] for s in spec['search_seeds']]


def _search_job(args):
    base, spec, group, window, response, control, search_seed = args
    cfg_spec = {**spec, 'search_seed': search_seed}
    c, r = A.lookup(A.CONTROLS, control), A.lookup(RESPONSES, response)
    cfg = config(base, control, response, spec['throttle'], window)
    found = search(cfg, c, r, cfg_spec)
    return {'group': group, 'window': window, 'search_seed': search_seed, **found}


def search(cfg, control, response, spec):
    """A.search with a prepared config (A.search builds its own config without a window)."""
    import random
    rng = random.Random(f"{spec['search_seed']}|{control['name']}|{response['name']}")
    keys = list(spec['space'])
    cache, log = {}, []

    def objective(p):
        key = canonical(p)
        if key not in cache:
            value, rows = A.evaluate(cfg, spec['train_seeds'], control['gate'], p, spec['alert_penalty'])
            cache[key] = value
            log.append({'params': p, 'score': value, 'mean_D': round(statistics.mean(r['D'] for r in rows), 6),
                        'alert_rate': statistics.mean(r['alert'] for r in rows)})
        return cache[key]

    best, best_score = None, None
    for _ in range(spec['random_candidates']):
        p = {k: rng.choice(spec['space'][k]) for k in keys}
        s = objective(p)
        if best_score is None or s > best_score:
            best, best_score = p, s
    for _ in range(spec['local_steps']):
        k = rng.choice(keys)
        values = spec['space'][k]
        i = values.index(best[k])
        j = min(max(i + rng.choice([-1, 1]), 0), len(values) - 1) if k != 'lifetime_overlap' \
            else rng.randrange(len(values))
        p = {**best, k: values[j]}
        s = objective(p)
        if s > best_score:
            best, best_score = p, s
    return {'control': control['name'], 'response': response['name'], 'best_params': best,
            'train_score': best_score, 'evaluations': len(log), 'log': log}


def _held_out_job(args):
    base, spec, found = args
    c = A.lookup(A.CONTROLS, found['control'])
    cfg = config(base, found['control'], found['response'], spec['throttle'], found['window'])
    rows = [A.paired(cfg, s, c['gate'], found['best_params']) for s in spec['test_seeds']]
    label = f"robust|{found['group']}|{found['response']}|{found['control']}|{found['search_seed']}"
    out = {k: found[k] for k in ('group', 'window', 'response', 'control', 'search_seed', 'best_params',
                                 'train_score', 'evaluations')}
    out['test'] = A.summarize_rows(rows, spec, label)
    disp = [display_row(cfg, s, c['gate'], found['best_params']) for s in spec['test_seeds']]
    gains = [d['display_gain'] for d in disp]
    out['display'] = {'mean_display_gain': round(statistics.mean(gains), 6),
                      'display_gain_ci95': A.bootstrap(gains, spec['bootstrap_resamples'], label + '|display'),
                      'mean_wealth': round(statistics.mean(d['wealth'] for d in disp), 6),
                      'positive_display_gain': sum(g > 0 for g in gains)}
    return out, rows, disp


def legit_episode(cfg, seed, strategy, aware):
    m = WindowMarket(cfg, seed, aware, 'off')
    times = list(range(0, cfg['session_seconds'], cfg['decision_interval']))
    for step, t in enumerate(times):
        m.advance(t)
        for a in control_actions(m, strategy, step, len(times)):
            m.act(a)
    m.advance(cfg['session_seconds'])
    return m.metrics()['wealth_fundamental']


def _legit_job(args):
    base, spec, strategy, response, window = args
    cfg = config(base, 'none', response, False, window)
    d = [round(legit_episode(cfg, s, strategy, True) - legit_episode(cfg, s, strategy, False), 6)
         for s in spec['legit_seeds']]
    label = f"robust|legit|{strategy}|{response}|{window}"
    return {'strategy': strategy, 'response': response, 'window': window, 'mean_D': round(statistics.mean(d), 6),
            'D_ci95': A.bootstrap(d, spec['bootstrap_resamples'], label), 'positive_D': sum(x > 0 for x in d),
            'D': d}


def legit_jobs(base, spec):
    return [(base, spec, s, r, w) for s in spec['legit_strategies'] for r in spec['legit_responses']
            for w in spec['legit_windows']]


def summarize(held):
    out = []
    keys = list(dict.fromkeys((h['group'], h['response'], h['control']) for h in held))
    for g, r, c in keys:
        runs = [h for h in held if (h['group'], h['response'], h['control']) == (g, r, c)]
        chosen = max(runs, key=lambda h: (h['train_score'], -SPEC['search_seeds'].index(h['search_seed'])))
        means = [h['test']['mean_D'] for h in runs]
        out.append({'group': g, 'window': chosen['window'], 'response': r, 'control': c,
                    'selected_search_seed': chosen['search_seed'], 'selected': chosen,
                    'restart_mean_D': means, 'restart_min': min(means), 'restart_max': max(means)})
    return out


def source_hashes():
    paths = [Path(__file__), Path(A.__file__), Path(A.__file__).with_name('sensitivity.py'), BASE] + \
        sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'responses': RESPONSES, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'searches': len(cells(SPEC)), 'legit_cells': len(legit_jobs(None, SPEC))}))


def evaluate_all(p, searches, workers):
    base, spec = p['config'], p['spec']
    with ProcessPoolExecutor(max_workers=workers) as pool:
        held = list(pool.map(_held_out_job, [(base, spec, s) for s in searches]))
        legit = list(pool.map(_legit_job, legit_jobs(base, spec)))
    return held, legit


def strip_legit(x):
    return {k: v for k, v in x.items() if k != 'D'}


def run(out, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    base, spec = p['config'], p['spec']
    with ProcessPoolExecutor(max_workers=workers) as pool:
        searches = list(pool.map(_search_job, [(base, spec, *c) for c in cells(spec)]))
    held, legit = evaluate_all(p, searches, workers)
    with (out / 'search_log.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for s in searches:
            stream.write((canonical(s) + '\n').encode())
    with (out / 'episodes.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for h, rows, disp in held:
            stream.write((canonical({'kind': 'held_out', **h, 'rows': rows, 'display_rows': disp}) + '\n').encode())
        for x in legit:
            stream.write((canonical({'kind': 'legit', **x}) + '\n').encode())
    summary = {'cells': summarize([h for h, _, _ in held]), 'legit': [strip_legit(x) for x in legit],
               'llm_episodes': 0,
               'inference_scope': 'Bootstrap 95% intervals over 100 held-out market seeds for fixed policies; '
                                  'restart spread shows search variability, not a confidence interval.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps([{k: c[k] for k in ('group', 'response', 'control', 'restart_mean_D')} for c in summary['cells']]))


def verify(out, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    with gzip.open(out / 'search_log.jsonl.gz', 'rt') as stream:
        searches = [json.loads(line) for line in stream]
    held, legit = evaluate_all(p, searches, workers)
    saved = json.loads((out / 'summary.json').read_text())
    assert json.loads(json.dumps(summarize([h for h, _, _ in held]))) == saved['cells']
    assert json.loads(json.dumps([strip_legit(x) for x in legit])) == saved['legit']
    result = {'held_out_policies_rerun': len(held), 'legit_cells_rerun': len(legit),
              'summary_reproduced': True, 'source_and_evidence_hashes_verified': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    {'plan': lambda: plan(args.output), 'run': lambda: run(args.output, args.workers),
     'verify': lambda: verify(args.output, args.workers)}[args.command]()
