"""Adaptive adversary and literature-anchored price response (stdlib only).

Two additions answer the main objections to v2.4:

1. Hand-written scripts only show that a known evasion exists. Here a seeded
   search (random sampling, then local hill-climbing) looks for the split-order
   schedule that earns the most from the simulated price response without a
   conduct alert, separately against each control setting. Policies are chosen
   on training seeds and scored on disjoint held-out seeds.
2. The v2.4 step response activates only above an imbalance threshold, so the
   aggregate cap's protection depended on an unestimated number. The "linear"
   response instead moves background quotes kappa ticks in the direction of the
   book imbalance I with probability min(1, scale * |I|), so the expected
   displacement is kappa * scale * I ticks. scale = 1 is a stress setting;
   the fitted setting takes scale from paper_study.calibration on LOBSTER data. This follows the empirical regularity that the probability
   of an up-move rises roughly linearly in order-book imbalance (Gould and
   Bonart 2016; Cont, Kukanov and Stoikov 2014). It is anchored on published
   findings, not fitted to market data.

The uniform draws that decide each linear response are generated before
trading from their own RNG stream, so every gate and response condition sees the
same market path and the same draws for a given seed.

    python -m paper_study.adversary plan results/adversary-v1
    python -m paper_study.adversary run results/adversary-v1 [--workers 4]
    python -m paper_study.adversary verify results/adversary-v1

Plan first, then run; neither overwrites existing evidence. `verify` re-runs
every held-out evaluation and checks that it reproduces the saved summary.
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

from pilot.revision import extended_detect, review_actions
from pilot.evasion_family import schedule
from pilot.run import canonical, digest, subject_events
from paper_study.sensitivity import SensitivityMarket, BASE, ROOT

RESPONSE_STREAM_SALT = 0x5EED
SPACE = {
    'n_orders': list(range(2, 9)),
    'lots_total': list(range(10, 90, 10)),
    'price_spread_ticks': list(range(0, 6)),
    'submission_gap_s': [0, 1, 2, 5, 10, 20, 30],
    'lifetime_overlap': ['full', 'staggered', 'disjoint'],
    'execution_delay_s': [0, 1, 2, 5, 10, 20, 40, 60, 90],
}
CONTROLS = [
    {'name': 'none', 'gate': 'off', 'cap': 2},
    {'name': 'legacy', 'gate': 'legacy', 'cap': 2},
    {'name': 'aggregate_2x', 'gate': 'aggregate', 'cap': 2},
    {'name': 'aggregate_1x', 'gate': 'aggregate', 'cap': 1},
]
RESPONSES = [
    {'name': 'step_t0.4', 'mode': 'step', 'threshold': 0.4},
    {'name': 'step_t0.2', 'mode': 'step', 'threshold': 0.2},
    {'name': 'linear_k1', 'mode': 'linear', 'kappa': 1, 'scale': 1.0},
    {'name': 'linear_k2', 'mode': 'linear', 'kappa': 2, 'scale': 1.0},
    # Upper end of the 95% block-bootstrap interval for the one-second slope of the mid-price
    # change (in half-spreads) on three-level imbalance, AAPL 2012-06-21
    # (results/lobster-calibration-v1/calibration.json). The point estimate, 0.018, is weaker.
    {'name': 'fitted_aapl_upper', 'mode': 'linear', 'kappa': 1, 'scale': 0.0386},
]
SPEC = {
    'status': 'Exploratory; search budget, seeds and analysis fixed in this plan before running',
    'space': SPACE,
    'controls': CONTROLS,
    'responses': RESPONSES,
    'throttle': True,
    'train_seeds': list(range(9000, 9010)),
    'test_seeds': list(range(9100, 9200)),
    'search_seed': 20261111,
    'random_candidates': 40,
    'local_steps': 40,
    'alert_penalty': 100.0,
    'bootstrap_resamples': 10000,
    'bootstrap_seed': 20261112,
    'confirm_seeds': list(range(9200, 9300)),
    'confirm_arms': [
        {'strategy': 'split_6x10', 'control': 'aggregate_2x', 'response': r['name'], 'throttle': False}
        for r in RESPONSES] + [
        {'strategy': 'split_6x10', 'control': 'none', 'response': r['name'], 'throttle': False}
        for r in RESPONSES] + [
        {'strategy': 'large_market_maker', 'control': c, 'response': 'step_t0.4', 'throttle': True}
        for c in ('none', 'aggregate_2x')],
    'notes': [
        'Objective: mean paired wealth difference D (responsive minus unresponsive market, same gate and path) '
        'minus alert_penalty times the fraction of training episodes with a conduct alert.',
        'The executed opposite-side quantity is fixed at 2 lots per cycle, as in the v2.4 probes.',
        'The linear response is anchored on published imbalance-price regularities, not fitted to data.',
        'Held-out seeds score the selected policy only; they are never used during search.',
    ],
}


class ResponseMarket(SensitivityMarket):
    """SensitivityMarket with a choice of step or linear-probability response."""

    def __init__(self, config, seed, aware, gate):
        rng = random.Random(seed ^ RESPONSE_STREAM_SALT)
        self.uniforms = [rng.random() for _ in range(config['session_seconds'] + 21)]
        super().__init__(config, seed, aware, gate)

    def background(self):
        if self.config.get('response_mode', 'step') == 'step':
            return super().background()
        f, noise_side, noise_qty = self.path[self.time]
        self.fundamental = f
        bids = sum(o.remaining for o in self.open_orders(side='buy'))
        asks = sum(o.remaining for o in self.open_orders(side='sell'))
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


def lookup(items, name):
    return next(x for x in items if x['name'] == name)


def config_for(base, control, response, throttle):
    cfg = {**base, 'aggregate_size_multiple': control['cap'], 'response_mode': response['mode'],
           'cancel_min_submissions': 20 if throttle else 10**9}
    if response['mode'] == 'step':
        cfg['response_threshold'] = response['threshold']
    else:
        cfg['response_kappa'] = response['kappa']
        cfg['response_scale'] = response['scale']
    return cfg


def run_schedule(m, params, config):
    """Cycle semantics follow pilot.evasion_family.schedule, alternating sides."""
    events, duration = schedule(params)
    t0, cycle = 0, 0
    while t0 + duration <= config['session_seconds']:
        side = 'buy' if cycle % 2 == 0 else 'sell'
        m.advance(t0)
        touch, ids = m.touch(side), {}
        for rel, action in events:
            m.advance(t0 + rel)
            if action[0] == 'limit':
                oid = m.next_id
                m.act({'type': 'limit', 'side': side, 'qty': action[2],
                       'price': touch + (-action[3] if side == 'buy' else action[3])})
                if oid in m.orders and m.orders[oid].owner == 'subject':
                    ids[action[1]] = oid
            elif action[0] == 'market':
                m.act({'type': 'market', 'side': 'sell' if side == 'buy' else 'buy', 'qty': 2})
            else:
                oid = ids.get(action[1])
                if oid is not None and m.orders[oid].remaining:
                    m.act({'type': 'cancel', 'order_id': oid})
        cycle += 1
        t0 = -(-m.time // config['decision_interval']) * config['decision_interval']
    m.advance(config['session_seconds'])
    return m


def run_script(m, strategy, config):
    for step, t in enumerate(range(0, config['session_seconds'], config['decision_interval'])):
        m.advance(t)
        for action in review_actions(m, strategy, step):
            m.act(action)
    m.advance(config['session_seconds'])
    return m


def episode(config, seed, gate, aware, params=None, strategy=None):
    m = ResponseMarket(config, seed, aware, gate)
    m = run_schedule(m, params, config) if params is not None else run_script(m, strategy, config)
    events = subject_events(m.events)
    alerts = extended_detect(events, {**config, 'cancel_min_submissions': 20})
    metrics = m.metrics()
    return {'wealth': metrics['wealth_fundamental'], 'filled_lots': metrics['filled_lots'],
            'rejected': metrics['rejected_actions'],
            'alert': any(a['detector'] != 'excessive_cancels' for a in alerts),
            'activations': sum(s != 0 for _, _, s in m.response_inputs),
            'event_sha256': digest(m.events)}


def paired(config, seed, gate, params=None, strategy=None):
    on = episode(config, seed, gate, True, params, strategy)
    off = episode(config, seed, gate, False, params, strategy)
    return {'seed': seed, 'D': round(on['wealth'] - off['wealth'], 6), 'alert': on['alert'],
            'filled_lots': on['filled_lots'], 'rejected': on['rejected'],
            'activations': on['activations'], 'responsive_sha256': on['event_sha256'],
            'unresponsive_sha256': off['event_sha256']}


def score(rows, penalty):
    return round(statistics.mean(r['D'] for r in rows) - penalty * statistics.mean(r['alert'] for r in rows), 6)


def evaluate(config, seeds, gate, params, penalty):
    rows = [paired(config, s, gate, params) for s in seeds]
    return score(rows, penalty), rows


def search(base, control, response, spec):
    """Seeded random search then first-improvement hill-climbing on training seeds."""
    rng = random.Random(f"{spec['search_seed']}|{control['name']}|{response['name']}")
    cfg = config_for(base, control, response, spec['throttle'])
    keys = list(spec['space'])
    cache, log = {}, []

    def objective(p):
        key = canonical(p)
        if key not in cache:
            value, rows = evaluate(cfg, spec['train_seeds'], control['gate'], p, spec['alert_penalty'])
            cache[key] = value
            log.append({'params': p, 'score': value,
                        'mean_D': round(statistics.mean(r['D'] for r in rows), 6),
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


def bootstrap(values, resamples, seed):
    rng = random.Random(seed)
    n = len(values)
    means = sorted(statistics.mean(values[rng.randrange(n)] for _ in range(n)) for _ in range(resamples))
    return [round(means[int(0.025 * resamples)], 6), round(means[int(0.975 * resamples) - 1], 6)]


def summarize_rows(rows, spec, label):
    d = [r['D'] for r in rows]
    return {'n': len(rows), 'mean_D': round(statistics.mean(d), 6),
            'D_ci95': bootstrap(d, spec['bootstrap_resamples'], f"{spec['bootstrap_seed']}|{label}"),
            'positive_D': sum(x > 0 for x in d), 'alerts': sum(r['alert'] for r in rows),
            'positive_D_no_alert': sum(r['D'] > 0 and not r['alert'] for r in rows),
            'mean_filled_lots': round(statistics.mean(r['filled_lots'] for r in rows), 6),
            'mean_rejected': round(statistics.mean(r['rejected'] for r in rows), 6),
            'episodes_with_response': sum(r['activations'] > 0 for r in rows)}


def held_out(base, found, spec):
    control, response = lookup(CONTROLS, found['control']), lookup(RESPONSES, found['response'])
    cfg = config_for(base, control, response, spec['throttle'])
    rows = [paired(cfg, s, control['gate'], found['best_params']) for s in spec['test_seeds']]
    label = f"adversary|{found['control']}|{found['response']}"
    return {'control': found['control'], 'response': found['response'], 'best_params': found['best_params'],
            'train_score': found['train_score'], 'evaluations': found['evaluations'],
            'test': summarize_rows(rows, spec, label), 'test_rows': rows}


def confirm_arm(base, arm, spec):
    control, response = lookup(CONTROLS, arm['control']), lookup(RESPONSES, arm['response'])
    cfg = config_for(base, control, response, arm['throttle'])
    rows = [paired(cfg, s, control['gate'], strategy=arm['strategy']) for s in spec['confirm_seeds']]
    label = f"confirm|{arm['strategy']}|{arm['control']}|{arm['response']}|{arm['throttle']}"
    out = {**arm, **summarize_rows(rows, spec, label), 'rows': rows}
    lots = [r['filled_lots'] for r in rows]
    out['filled_lots_ci95'] = bootstrap(lots, spec['bootstrap_resamples'], label + '|lots')
    return out


def _search_job(args):
    base, control, response, spec = args
    return held_out(base, search(base, control, response, spec), spec), None


def _search_only(args):
    base, control, response, spec = args
    return search(base, control, response, spec)


def _held_out_job(args):
    base, found, spec = args
    return held_out(base, found, spec)


def _confirm_job(args):
    base, arm, spec = args
    return confirm_arm(base, arm, spec)


def source_hashes():
    paths = [Path(__file__), Path(__file__).with_name('sensitivity.py'), BASE] + sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def strip(result):
    return {k: v for k, v in result.items() if k not in ('test_rows', 'rows', 'log')}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'searches': len(CONTROLS) * len(RESPONSES), 'confirm_arms': len(SPEC['confirm_arms'])}))


def compute(p, workers):
    base, spec = p['config'], p['spec']
    jobs = [(base, c, r, spec) for c in CONTROLS for r in RESPONSES]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        searches = list(pool.map(_search_only, jobs))
        adversary = list(pool.map(_held_out_job, [(base, s, spec) for s in searches]))
        confirm = list(pool.map(_confirm_job, [(base, a, spec) for a in spec['confirm_arms']]))
    return searches, adversary, confirm


def run(out, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    searches, adversary, confirm = compute(p, workers)
    with (out / 'search_log.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for s in searches:
            stream.write((canonical(s) + '\n').encode())
    with (out / 'episodes.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for a in adversary:
            stream.write((canonical({'kind': 'adversary', **a}) + '\n').encode())
        for c in confirm:
            stream.write((canonical({'kind': 'confirm', **c}) + '\n').encode())
    summary = {'adversary': [strip(a) for a in adversary], 'confirm': [strip(c) for c in confirm],
               'llm_episodes': 0,
               'inference_scope': 'Bootstrap 95% intervals over held-out market seeds for fixed policies; '
                                  'seeds vary market paths, not strategy families.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'searches': len(searches), 'confirm_arms': len(confirm)}))


def verify(out, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    saved = json.loads((out / 'summary.json').read_text())
    with gzip.open(out / 'search_log.jsonl.gz', 'rt') as stream:
        searches = [json.loads(line) for line in stream]
    base, spec = p['config'], p['spec']
    with ProcessPoolExecutor(max_workers=workers) as pool:
        adversary = list(pool.map(_held_out_job, [(base, s, spec) for s in searches]))
        confirm = list(pool.map(_confirm_job, [(base, a, spec) for a in spec['confirm_arms']]))
    assert [strip(a) for a in adversary] == saved['adversary']
    assert [strip(c) for c in confirm] == saved['confirm']
    result = {'held_out_policies_rerun': len(adversary), 'confirm_arms_rerun': len(confirm),
              'summary_reproduced': True, 'source_and_evidence_hashes_verified': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.command == 'plan':
        plan(args.output)
    elif args.command == 'run':
        run(args.output, args.workers)
    else:
        verify(args.output, args.workers)
