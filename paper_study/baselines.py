"""Baseline detectors beside the implemented conduct rules (stdlib only).

The conduct rules of pilot.revision were written for this project, so their
failure against the adversary could reflect a weak rule rather than a hard
problem. Two independent baselines are added:

  * OTR: the order-to-trade ratio used by EU venues under RTS 9 (Commission
    Delegated Regulation (EU) 2017/566): orders entered plus cancellations,
    divided by the number of the account's transactions.
  * Learned: a logistic-regression detector on per-episode order-stream
    features, trained on labelled episodes, in the spirit of the detection-
    evasion framework of Wang and Wellman (IJCAI 2020).

Both thresholds are set on training seeds so that at most 5% of legitimate
training episodes alert. The study then measures, on disjoint seeds:
  1. false alerts on the five legitimate scripts,
  2. detection of fresh random schedules from the evasion family,
  3. detection of the 24 schedules the adversary found against the conduct rules
     (results/adversary-v1), and
  4. what a search finds when it must also evade both baselines.

    python -m paper_study.baselines plan results/baselines-v1
    python -m paper_study.baselines run results/baselines-v1 [--workers 4]
    python -m paper_study.baselines verify results/baselines-v1
"""
import argparse
import gzip
import hashlib
import json
import math
import platform
import random
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pilot.negative_controls import control_actions, max_displayed
from pilot.revision import extended_detect
from pilot.run import canonical, subject_events
from pilot.evasion_family import schedule
from paper_study.adversary import CONTROLS, RESPONSES, SPACE, ResponseMarket, bootstrap, config_for, lookup
from paper_study.sensitivity import BASE, ROOT

LEGIT = ['vwap_executor', 'iceberg', 'market_maker', 'large_market_maker', 'patient_large_maker']
FEATURES = ['cancel_ratio', 'log_otr', 'max_display_ratio', 'cancel_after_fill', 'short_lived',
            'passive_fill_ratio', 'aggressor_share']
SPEC = {
    'status': 'Exploratory; seeds, training design, thresholds and search budget fixed in this plan before running',
    'train_seeds': list(range(9000, 9010)),
    'test_seeds': list(range(9100, 9200)),
    'train_schedules': 60,
    'test_schedules': 100,
    'schedule_seed': 20261114,
    'training_condition': {'control': 'none', 'response': 'fitted_msft', 'throttle': True},
    'false_alert_target': 0.05,
    'logistic': {'l2': 0.01, 'learning_rate': 0.1, 'iterations': 3000},
    'cancel_after_fill_seconds': 30,
    'short_lived_seconds': 60,
    'adversary_summary': 'results/adversary-v1/summary.json',
    # The adaptive search may also change the executed size, which the family fixes at 2 lots.
    'execution_lots': [2, 5, 10, 20],
    'adaptive': {'controls': ['none', 'aggregate_2x'], 'responses': ['linear_k1', 'fitted_msft'],
                 'random_candidates': 40, 'local_steps': 40, 'alert_penalty': 100.0, 'search_seed': 20261115},
    'bootstrap_resamples': 10000,
    'bootstrap_seed': 20261116,
}


# ---------------------------------------------------------------- features

def features(events, spec):
    ev = subject_events(events)
    submits = [e for e in ev if e['kind'] == 'submit']
    passive = {e['order_id']: e for e in submits if not e['marketable']}
    cancels = [e for e in ev if e['kind'] == 'cancel']
    fills = [e for e in ev if e['kind'] == 'fill']
    own_fill_times = {'buy': [], 'sell': []}
    for f in fills:
        own_fill_times['buy' if f['buyer'] == 'subject' else 'sell'].append(f['time'])
    passive_qty = sum(e['qty'] for e in passive.values())
    cancelled_qty = sum(e['qty'] for e in cancels if e['order_id'] in passive)
    after_fill = short = 0
    for c in cancels:
        if c['order_id'] not in passive:
            continue
        opposite = 'sell' if c['side'] == 'buy' else 'buy'
        if any(0 <= c['time'] - t <= spec['cancel_after_fill_seconds'] for t in own_fill_times[opposite]):
            after_fill += c['qty']
        if c['time'] - passive[c['order_id']]['time'] <= spec['short_lived_seconds']:
            short += c['qty']
    passive_filled = sum(f['qty'] for f in fills if f['maker_id'] in passive)
    depths = [e['reference_depth'] for e in submits] or [1]
    otr = (len(submits) + len(cancels)) / max(1, len(fills))
    return {'cancel_ratio': cancelled_qty / passive_qty if passive_qty else 0.0,
            'log_otr': math.log(otr),
            'max_display_ratio': max_displayed(ev) / statistics.median(depths),
            'cancel_after_fill': after_fill / cancelled_qty if cancelled_qty else 0.0,
            'short_lived': short / cancelled_qty if cancelled_qty else 0.0,
            'passive_fill_ratio': passive_filled / passive_qty if passive_qty else 0.0,
            'aggressor_share': (sum(f['qty'] for f in fills if f['aggressor'] == 'subject') /
                                max(1, sum(f['qty'] for f in fills))),
            'otr': otr}


def run_schedule(m, params, config):
    """paper_study.adversary.run_schedule with an optional execution size (default 2 lots)."""
    events, duration = schedule(params)
    lots = params.get('execution_lots', 2)
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
                m.act({'type': 'market', 'side': 'sell' if side == 'buy' else 'buy', 'qty': lots})
            else:
                oid = ids.get(action[1])
                if oid is not None and m.orders[oid].remaining:
                    m.act({'type': 'cancel', 'order_id': oid})
        cycle += 1
        t0 = -(-m.time // config['decision_interval']) * config['decision_interval']
    m.advance(config['session_seconds'])
    return m


def run_legit(m, strategy, config):
    times = list(range(0, config['session_seconds'], config['decision_interval']))
    for step, t in enumerate(times):
        m.advance(t)
        for a in control_actions(m, strategy, step, len(times)):
            m.act(a)
    m.advance(config['session_seconds'])
    return m


def condition(base, control, response, throttle):
    c = lookup(CONTROLS, control)
    return config_for(base, c, lookup(RESPONSES, response), throttle), c['gate']


def observe(cfg, gate, seed, spec, params=None, strategy=None, aware=True):
    m = ResponseMarket(cfg, seed, aware, gate)
    m = run_schedule(m, params, cfg) if params is not None else run_legit(m, strategy, cfg)
    alerts = extended_detect(subject_events(m.events), {**cfg, 'cancel_min_submissions': 20})
    return {'seed': seed, 'conduct_alert': any(a['detector'] != 'excessive_cancels' for a in alerts),
            'wealth': m.metrics()['wealth_fundamental'], **features(m.events, spec)}


def draw_schedules(rng, n):
    return [{k: rng.choice(v) for k, v in SPACE.items()} for _ in range(n)]


# ---------------------------------------------------------------- detectors

def standardize(rows):
    stats = {}
    for k in FEATURES:
        vals = [r[k] for r in rows]
        stats[k] = (statistics.mean(vals), statistics.pstdev(vals) or 1.0)
    return stats


def fit_logistic(rows, labels, spec):
    stats = standardize(rows)
    x = [[(r[k] - stats[k][0]) / stats[k][1] for k in FEATURES] for r in rows]
    pos = sum(labels)
    weights = [0.5 / pos if y else 0.5 / (len(labels) - pos) for y in labels]  # balanced classes
    w, b = [0.0] * len(FEATURES), 0.0
    lr, l2 = spec['logistic']['learning_rate'], spec['logistic']['l2']
    for _ in range(spec['logistic']['iterations']):
        gw, gb = [l2 * wi for wi in w], 0.0
        for xi, yi, wt in zip(x, labels, weights):
            z = b + sum(a * c for a, c in zip(w, xi))
            p = 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))
            for j, v in enumerate(xi):
                gw[j] += wt * (p - yi) * v
            gb += wt * (p - yi)
        w = [wi - lr * g for wi, g in zip(w, gw)]
        b -= lr * gb
    return {'features': FEATURES, 'mean': [stats[k][0] for k in FEATURES], 'sd': [stats[k][1] for k in FEATURES],
            'weights': [round(v, 9) for v in w], 'bias': round(b, 9)}


def logit(model, row):
    return model['bias'] + sum(w * (row[k] - m) / s for w, k, m, s in
                               zip(model['weights'], model['features'], model['mean'], model['sd']))


def quantile_threshold(values, target):
    """Smallest threshold with at most `target` of values strictly above it."""
    values = sorted(values)
    allowed = math.floor(target * len(values))
    return values[len(values) - 1 - allowed]


def flags(row, det):
    return {'conduct': row['conduct_alert'], 'otr': row['otr'] > det['otr_threshold'],
            'learned': logit(det['model'], row) > det['learned_threshold']}


def any_alert(row, det):
    f = flags(row, det)
    return f['conduct'] or f['otr'] or f['learned']


# ---------------------------------------------------------------- jobs

def _observe_job(args):
    return observe(*args)


def _paired_job(args):
    cfg, gate, seed, spec, params, det = args
    on = observe(cfg, gate, seed, spec, params=params)
    off = observe(cfg, gate, seed, spec, params=params, aware=False)
    return {'seed': seed, 'D': round(on['wealth'] - off['wealth'], 6), 'alert': any_alert(on, det),
            **{f'{k}_alert': v for k, v in flags(on, det).items()}}


def adaptive_search(base, control, response, spec, det, pool):
    a = spec['adaptive']
    rng = random.Random(f"{a['search_seed']}|{control}|{response}")
    cfg, gate = condition(base, control, response, True)
    cache = {}

    def objective(p):
        key = canonical(p)
        if key not in cache:
            rows = list(pool.map(_paired_job, [(cfg, gate, s, spec, p, det) for s in spec['train_seeds']]))
            cache[key] = round(statistics.mean(r['D'] for r in rows) -
                               a['alert_penalty'] * statistics.mean(r['alert'] for r in rows), 6)
        return cache[key]
    space = {**SPACE, 'execution_lots': spec['execution_lots']}
    keys = list(space)
    best, best_score = None, None
    for _ in range(a['random_candidates']):
        p = {k: rng.choice(space[k]) for k in keys}
        s = objective(p)
        if best_score is None or s > best_score:
            best, best_score = p, s
    for _ in range(a['local_steps']):
        k = rng.choice(keys)
        values = space[k]
        i = values.index(best[k])
        j = min(max(i + rng.choice([-1, 1]), 0), len(values) - 1) if k != 'lifetime_overlap' \
            else rng.randrange(len(values))
        p = {**best, k: values[j]}
        s = objective(p)
        if s > best_score:
            best, best_score = p, s
    rows = list(pool.map(_paired_job, [(cfg, gate, s, spec, best, det) for s in spec['test_seeds']]))
    d = [r['D'] for r in rows]
    label = f"{spec['bootstrap_seed']}|adaptive|{control}|{response}"
    return {'control': control, 'response': response, 'best_params': best, 'train_score': best_score,
            'evaluations': len(cache),
            'test': {'n': len(rows), 'mean_D': round(statistics.mean(d), 6),
                     'D_ci95': bootstrap(d, spec['bootstrap_resamples'], label),
                     'positive_D': sum(x > 0 for x in d), 'any_alert': sum(r['alert'] for r in rows),
                     'conduct_alerts': sum(r['conduct_alert'] for r in rows),
                     'otr_alerts': sum(r['otr_alert'] for r in rows),
                     'learned_alerts': sum(r['learned_alert'] for r in rows)},
            'rows': rows}


def rate(rows, det, key):
    return sum(flags(r, det)[key] for r in rows)


def compute(p, workers):
    base, spec = p['config'], p['spec']
    rng = random.Random(spec['schedule_seed'])
    train_sched, test_sched = draw_schedules(rng, spec['train_schedules']), draw_schedules(rng, spec['test_schedules'])
    tc = spec['training_condition']
    cfg, gate = condition(base, tc['control'], tc['response'], tc['throttle'])
    found = json.loads((ROOT / spec['adversary_summary']).read_text())['adversary']
    with ProcessPoolExecutor(max_workers=workers) as pool:
        train_pos = list(pool.map(_observe_job, [(cfg, gate, s, spec, q) for q in train_sched
                                                for s in spec['train_seeds']]))
        train_neg = list(pool.map(_observe_job, [(cfg, gate, s, spec, None, st) for st in LEGIT
                                                for s in spec['train_seeds']]))
        model = fit_logistic(train_pos + train_neg, [1] * len(train_pos) + [0] * len(train_neg), spec)
        det = {'model': model,
               'otr_threshold': quantile_threshold([r['otr'] for r in train_neg], spec['false_alert_target']),
               'learned_threshold': quantile_threshold([logit(model, r) for r in train_neg],
                                                       spec['false_alert_target'])}
        test_legit = {st: list(pool.map(_observe_job, [(cfg, gate, s, spec, None, st) for s in spec['test_seeds']]))
                      for st in LEGIT}
        test_random = list(pool.map(_observe_job, [(cfg, gate, s, spec, q)
                                                   for q, s in zip(test_sched, spec['test_seeds'])]))
        searched = []
        for f in found:
            c, g = condition(base, f['control'], f['response'], True)
            rows = list(pool.map(_observe_job, [(c, g, s, spec, f['best_params']) for s in spec['test_seeds']]))
            searched.append({'control': f['control'], 'response': f['response'], 'rows': rows})
        adaptive = [adaptive_search(base, c, r, spec, det, pool)
                    for c in spec['adaptive']['controls'] for r in spec['adaptive']['responses']]
    return {'detectors': det, 'train_positive': train_pos, 'train_negative': train_neg, 'test_legit': test_legit,
            'test_random': test_random, 'searched': searched, 'adaptive': adaptive}


def summarize(ev):
    det = ev['detectors']

    def counts(rows):
        return {'n': len(rows), 'conduct': rate(rows, det, 'conduct'), 'otr': rate(rows, det, 'otr'),
                'learned': rate(rows, det, 'learned'), 'any': sum(any_alert(r, det) for r in rows)}
    searched = [{'control': s['control'], 'response': s['response'], **counts(s['rows'])} for s in ev['searched']]
    return {'detectors': det,
            'training': {'positive': counts(ev['train_positive']), 'negative': counts(ev['train_negative'])},
            'legit_false_alerts': {st: counts(rows) for st, rows in ev['test_legit'].items()},
            'random_schedules': counts(ev['test_random']),
            'searched_schedules': searched,
            'searched_totals': {k: sum(s[k] for s in searched) for k in ('n', 'conduct', 'otr', 'learned', 'any')},
            'adaptive': [{k: v for k, v in a.items() if k != 'rows'} for a in ev['adaptive']]}


def source_hashes():
    paths = [Path(__file__), Path(__file__).with_name('adversary.py'), Path(__file__).with_name('sensitivity.py'),
             BASE, ROOT / SPEC['adversary_summary']] + sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'adaptive_searches': len(SPEC['adaptive']['controls']) * len(SPEC['adaptive']['responses'])}))


def run(out, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    ev = compute(p, workers)
    with (out / 'episodes.json.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        stream.write(canonical(ev).encode())
    summary = {**summarize(ev), 'llm_episodes': 0,
               'inference_scope': 'Detection counts over fixed seeds; schedules come from one authored family.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(summary['searched_totals']))


def verify(out, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    ev = compute(p, workers)
    with gzip.open(out / 'episodes.json.gz', 'rt') as stream:
        assert json.loads(stream.read()) == json.loads(canonical(ev))
    saved = json.loads((out / 'summary.json').read_text())
    assert json.loads(json.dumps(summarize(ev))) == {k: v for k, v in saved.items()
                                                     if k not in ('llm_episodes', 'inference_scope')}
    result = {'summary_reproduced': True, 'source_and_evidence_hashes_verified': True}
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
