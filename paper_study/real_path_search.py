"""Adversary search rerun on real price paths (stdlib only).

results/empirical-paths-v1 replayed policies that were selected on synthetic
paths. On real MSFT and INTC paths the ungated policies kept their gain but
alerted in about half the windows or more, so that study could not say whether
evasion fails on real prices or only fails for policies tuned to a calm path.
This study lets the search see real prices.

Each LOBSTER sample gives 30 windows of 740 seconds (as in empirical-paths-v1:
window k uses noise and response seed 9100+k). The windows are split into three
folds by k mod 3. For each fold, the search of paper_study.adversary (same
space, budget, alert penalty and algorithm) runs on the 20 windows outside the
fold, and the selected policy is scored on the 10 windows inside it. Every
window therefore gets exactly one out-of-sample score, and the 30 scores are
compared, window by window, with the policy selected on synthetic paths in
results/adversary-v1 (the empirical-paths-v1 replay).

    python -m paper_study.real_path_search plan results/real-path-search-v1
    python -m paper_study.real_path_search run results/real-path-search-v1 --lobster DIR [--workers 4]
    python -m paper_study.real_path_search verify results/real-path-search-v1 --lobster DIR

DIR is the same LOBSTER folder that paper_study.empirical_paths takes.
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

from pilot.run import canonical
from paper_study import adversary as A
from paper_study import empirical_paths as E

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'config.json'
SPEC = {
    'status': 'Exploratory; folds, cells, search budget and analysis fixed in this plan before running',
    'cells': [
        {'ticker': 'MSFT', 'response': 'fitted_msft'},
        {'ticker': 'INTC', 'response': 'fitted_intc'},
        {'ticker': 'MSFT', 'response': 'linear_k1'},
    ],
    'controls': ['none', 'legacy', 'aggregate_2x', 'aggregate_1x'],
    'folds': 3,
    'seed_base': 9100,
    'search_seed': 20261114,
    'policy_source': 'results/adversary-v1/summary.json',
    'bootstrap_resamples': 10000,
    'bootstrap_seed': 20261115,
    'notes': [
        'Search space, random_candidates, local_steps, alert_penalty and throttle are taken from '
        'paper_study.adversary.SPEC unchanged; only the training paths differ.',
        'Window k belongs to fold k mod 3; a fold\'s policy is searched on the other 20 windows.',
        'Comparison: per-window difference in D and in alerts between the real-path policy and the '
        'synthetic-path policy, on the same window, seed, gate and response.',
    ],
}


def fold_of(k, spec):
    return k % spec['folds']


def objective_rows(cfg, gate, params, paths, ks, spec):
    return [{'window': k, **E.paired(cfg, spec['seed_base'] + k, gate, params, paths[k])} for k in ks]


def search(cfg, gate, paths, train, label, spec, adv=A.SPEC):
    """paper_study.adversary.search with real-path windows in place of training seeds."""
    rng = random.Random(f"{spec['search_seed']}|{label}")
    keys = list(adv['space'])
    cache, log = {}, []

    def objective(p):
        key = canonical(p)
        if key not in cache:
            rows = objective_rows(cfg, gate, p, paths, train, spec)
            value = A.score(rows, adv['alert_penalty'])
            cache[key] = value
            log.append({'params': p, 'score': value,
                        'mean_D': round(statistics.mean(r['D'] for r in rows), 6),
                        'alert_rate': statistics.mean(r['alert'] for r in rows)})
        return cache[key]

    best, best_score = None, None
    for _ in range(adv['random_candidates']):
        p = {k: rng.choice(adv['space'][k]) for k in keys}
        s = objective(p)
        if best_score is None or s > best_score:
            best, best_score = p, s
    for _ in range(adv['local_steps']):
        k = rng.choice(keys)
        values = adv['space'][k]
        i = values.index(best[k])
        j = min(max(i + rng.choice([-1, 1]), 0), len(values) - 1) if k != 'lifetime_overlap' \
            else rng.randrange(len(values))
        p = {**best, k: values[j]}
        s = objective(p)
        if s > best_score:
            best, best_score = p, s
    return best, best_score, log


def jobs_for(base, spec, paths):
    jobs = []
    for cell in spec['cells']:
        for c in spec['controls']:
            for f in range(spec['folds']):
                jobs.append((base, spec, cell, c, f, paths[cell['ticker']]))
    return jobs


def setting(base, cell, c):
    control, response = A.lookup(A.CONTROLS, c), A.lookup(A.RESPONSES, cell['response'])
    return A.config_for(base, control, response, A.SPEC['throttle']), control['gate']


def _search_job(job):
    base, spec, cell, c, f, paths = job
    cfg, gate = setting(base, cell, c)
    train = [k for k in range(len(paths)) if fold_of(k, spec) != f]
    label = f"{cell['ticker']}|{cell['response']}|{c}|fold{f}"
    best, best_score, log = search(cfg, gate, paths, train, label, spec)
    return {'ticker': cell['ticker'], 'response': cell['response'], 'control': c, 'fold': f,
            'best_params': best, 'train_score': best_score, 'evaluations': len(log), 'log': log}


def _test_job(job):
    """Held-out score of one fold's real-path policy and of the synthetic policy on the same windows."""
    base, spec, found, synthetic_params, paths = job
    cell = {'ticker': found['ticker'], 'response': found['response']}
    cfg, gate = setting(base, cell, found['control'])
    test = [k for k in range(len(paths)) if fold_of(k, spec) == found['fold']]
    real = objective_rows(cfg, gate, found['best_params'], paths, test, spec)
    synth = objective_rows(cfg, gate, synthetic_params, paths, test, spec)
    return [{'ticker': found['ticker'], 'response': found['response'], 'control': found['control'],
             'fold': found['fold'], 'window': r['window'], 'seed': r['seed'],
             'real': {k: r[k] for k in ('D', 'alert', 'filled_lots', 'rejected', 'activations')},
             'synthetic': {k: s[k] for k in ('D', 'alert', 'filled_lots', 'rejected', 'activations')}}
            for r, s in zip(real, synth)]


def summarize(rows, searches, spec):
    out = []
    for cell in spec['cells']:
        for c in spec['controls']:
            key = (cell['ticker'], cell['response'], c)
            sel = sorted((r for r in rows if (r['ticker'], r['response'], r['control']) == key),
                         key=lambda r: r['window'])
            label = '|'.join(key)
            boot = lambda xs, tag: A.bootstrap(xs, spec['bootstrap_resamples'],
                                               f"{spec['bootstrap_seed']}|{label}|{tag}")
            real = [r['real']['D'] for r in sel]
            synth = [r['synthetic']['D'] for r in sel]
            diff = [a - b for a, b in zip(real, synth)]
            alert_diff = [int(r['real']['alert']) - int(r['synthetic']['alert']) for r in sel]

            def side(name, d):
                return {'mean_D': round(statistics.mean(d), 6), 'D_ci95': boot(d, name),
                        'positive_D': sum(x > 0 for x in d),
                        'alerts': sum(r[name]['alert'] for r in sel),
                        'positive_D_no_alert': sum(r[name]['D'] > 0 and not r[name]['alert'] for r in sel),
                        'mean_filled_lots': round(statistics.mean(r[name]['filled_lots'] for r in sel), 6)}

            policies = [{'fold': s['fold'], 'best_params': s['best_params'], 'train_score': s['train_score'],
                         'evaluations': s['evaluations']}
                        for s in searches if (s['ticker'], s['response'], s['control']) == key]
            out.append({'ticker': cell['ticker'], 'response': cell['response'], 'control': c,
                        'windows': len(sel), 'real_path_policy': side('real', real),
                        'synthetic_policy': side('synthetic', synth),
                        'difference_mean_D': round(statistics.mean(diff), 6),
                        'difference_D_ci95': boot(diff, 'diff'),
                        'difference_alerts': sum(alert_diff),
                        'fold_policies': sorted(policies, key=lambda p: p['fold'])})
    return out


def load_paths(base, lobster, spec):
    paths, hashes = {}, {}
    for t in sorted({c['ticker'] for c in spec['cells']}):
        series, _, hashes[t] = E.day_series(lobster, t, E.SPEC)
        paths[t] = [E.tick_path(w, base['base_price']) for w in E.windows(series, E.SPEC)]
    return paths, hashes


def synthetic_policies(spec):
    s = json.loads((ROOT / spec['policy_source']).read_text())
    return {(a['control'], a['response']): a['best_params'] for a in s['adversary']}


def test_all(base, spec, searches, paths, workers):
    synth = synthetic_policies(spec)
    jobs = [(base, spec, s, synth[(s['control'], s['response'])], paths[s['ticker']]) for s in searches]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return [r for chunk in pool.map(_test_job, jobs) for r in chunk]


def source_hashes():
    paths = [Path(__file__), Path(A.__file__), Path(E.__file__),
             Path(A.__file__).with_name('calibration.py'), Path(A.__file__).with_name('sensitivity.py'),
             BASE] + sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'adversary_spec': A.SPEC, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'searches': len(SPEC['cells']) * len(SPEC['controls']) * SPEC['folds']}))


def strip(s):
    return {k: v for k, v in s.items() if k != 'log'}


def run(out, lobster, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    assert p['adversary_spec'] == json.loads(json.dumps(A.SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    base, spec = p['config'], p['spec']
    paths, hashes = load_paths(base, lobster, spec)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        searches = list(pool.map(_search_job, jobs_for(base, spec, paths)))
    rows = test_all(base, spec, searches, paths, workers)
    with (out / 'search_log.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for s in searches:
            stream.write((canonical(s) + '\n').encode())
    with (out / 'episodes.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for r in rows:
            stream.write((canonical(r) + '\n').encode())
    summary = {'cells': summarize(rows, searches, spec), 'lobster_sha256': hashes, 'llm_episodes': 0,
               'inference_scope': 'Cross-fitted: each of 30 real-path windows is scored by a policy searched on '
                                  'the other two folds. Bootstrap 95% intervals over windows; one trading day '
                                  'per stock.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps([{'cell': f"{c['ticker']}|{c['response']}|{c['control']}",
                       'real': [c['real_path_policy'][k] for k in ('mean_D', 'alerts', 'positive_D_no_alert')],
                       'synthetic': [c['synthetic_policy'][k] for k in ('mean_D', 'alerts', 'positive_D_no_alert')]}
                      for c in summary['cells']], indent=1))


def verify(out, lobster, workers):
    """Re-scores every held-out window from the saved policies and checks the saved summary."""
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    base, spec = p['config'], p['spec']
    saved = json.loads((out / 'summary.json').read_text())
    paths, hashes = load_paths(base, lobster, spec)
    assert hashes == saved['lobster_sha256'], 'LOBSTER files differ from the recorded ones'
    with gzip.open(out / 'search_log.jsonl.gz', 'rt') as stream:
        searches = [json.loads(line) for line in stream]
    rows = test_all(base, spec, searches, paths, workers)
    with gzip.open(out / 'episodes.jsonl.gz', 'rt') as stream:
        assert [json.loads(line) for line in stream] == json.loads(json.dumps(rows))
    assert json.loads(json.dumps(summarize(rows, searches, spec))) == saved['cells']
    result = {'held_out_windows_rerun': len(rows), 'summary_reproduced': True,
              'source_and_evidence_hashes_verified': True}
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
