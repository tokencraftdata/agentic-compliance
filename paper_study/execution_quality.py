"""Execution quality of legitimate traders under the controls (stdlib only).

Volume alone says little about whether a control harms a legitimate trader. This
runner scores the five legitimate scripts of pilot.negative_controls with
standard execution-quality measures, on 100 fresh seeds, under the price
responses of paper_study.adversary:

  executors (buy an 80-lot parent)
    * fill rate: filled lots / 80
    * implementation shortfall, ticks per parent lot: sum over fills of
      (price - arrival mid) + fees, plus (terminal fundamental - arrival mid)
      for the unfilled remainder (the cost of completing it at the close)
    * time to half fill, seconds (session length if never reached)
  market makers
    * filled lots and terminal wealth at the fundamental
    * effective edge per lot: signed (mid at fill - fill price)
    * 60-second markout per lot: signed (mid 60 s after the fill - fill price),
      which nets adverse selection out of the edge
    * mean absolute inventory over the session
  all strategies
    * rejected actions and whether a conduct alert fired

Every control arm is paired with the ungated arm on the same seed, and the
paired differences get 95% percentile bootstrap intervals.

    python -m paper_study.execution_quality plan results/execution-quality-v1
    python -m paper_study.execution_quality run results/execution-quality-v1 [--workers 4]
    python -m paper_study.execution_quality verify results/execution-quality-v1
"""
import argparse
import gzip
import hashlib
import json
import platform
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pilot.negative_controls import PARENT_LOTS, control_actions
from pilot.revision import extended_detect
from pilot.run import canonical, digest, subject_events
from paper_study.adversary import CONTROLS, RESPONSES, ResponseMarket, bootstrap, config_for, lookup
from paper_study.sensitivity import BASE, ROOT

STRATEGIES = ['vwap_executor', 'iceberg', 'market_maker', 'large_market_maker', 'patient_large_maker']
EXECUTORS = ('vwap_executor', 'iceberg')
ARMS = [  # (control, throttle); the first is the ungated baseline every other arm is paired with
    ('none', False),
    ('aggregate_2x', True),
    ('aggregate_2x', False),
    ('aggregate_1x', False),
]
SPEC = {
    'status': 'Exploratory; strategies, seeds, arms and measures fixed in this plan before running',
    'strategies': STRATEGIES,
    'arms': [{'control': c, 'throttle': t} for c, t in ARMS],
    'responses': ['step_t0.4', 'fitted_aapl', 'fitted_msft'],
    'seeds': list(range(9300, 9400)),
    'markout_seconds': 60,
    'bootstrap_resamples': 10000,
    'bootstrap_seed': 20261113,
    'notes': [
        'Responsive market only; the step response at t = 0.4 is the original v2.4 setting.',
        'Executor benchmarks use the mid at time 0, before the first action.',
        'Markouts after the session end use the last recorded mid.',
    ],
}
MEASURES = {
    'executor': ['fill_rate', 'shortfall_ticks_per_lot', 'time_to_half_s', 'rejected', 'alert'],
    'maker': ['filled_lots', 'wealth', 'edge_ticks_per_lot', 'markout_ticks_per_lot', 'mean_abs_inventory',
              'rejected', 'alert'],
}


def kind(strategy):
    return 'executor' if strategy in EXECUTORS else 'maker'


def episode(config, seed, gate, strategy, markout):
    m = ResponseMarket(config, seed, True, gate)
    arrival = (m.touch('buy') + m.touch('sell')) / 2
    times = list(range(0, config['session_seconds'], config['decision_interval']))
    for step, t in enumerate(times):
        m.advance(t)
        for a in control_actions(m, strategy, step, len(times)):
            m.act(a)
    m.advance(config['session_seconds'])
    mids = {s['time']: s['mid'] for s in m.samples}
    last = max(mids)

    def mid_at(t):
        t = min(t, last)
        while t not in mids:
            t -= 1
        return mids[t]
    fills = [e for e in m.events if e['kind'] == 'fill' and 'subject' in (e['buyer'], e['seller'])]
    metrics = m.metrics()
    alerts = extended_detect(subject_events(m.events), {**config, 'cancel_min_submissions': 20})
    row = {'seed': seed, 'rejected': metrics['rejected_actions'],
           'alert': any(a['detector'] != 'excessive_cancels' for a in alerts), 'event_sha256': digest(m.events)}
    lots = sum(e['qty'] for e in fills)
    if strategy in EXECUTORS:
        fee = config['fee_per_lot']
        cost = sum(e['qty'] * (e['price'] - arrival + fee) for e in fills)
        cost += (PARENT_LOTS - lots) * (m.fundamental - arrival)
        done, half = 0, config['session_seconds']
        for e in fills:
            done += e['qty']
            if done >= PARENT_LOTS / 2:
                half = e['time']
                break
        row.update({'fill_rate': round(lots / PARENT_LOTS, 6),
                    'shortfall_ticks_per_lot': round(cost / PARENT_LOTS, 6), 'time_to_half_s': half})
    else:
        edge = markout_sum = 0.0
        for e in fills:
            sign = 1 if e['buyer'] == 'subject' else -1
            edge += sign * e['qty'] * (mid_at(e['time']) - e['price'])
            markout_sum += sign * e['qty'] * (mid_at(e['time'] + markout) - e['price'])
        row.update({'filled_lots': lots, 'wealth': metrics['wealth_fundamental'],
                    'edge_ticks_per_lot': round(edge / lots, 6) if lots else 0.0,
                    'markout_ticks_per_lot': round(markout_sum / lots, 6) if lots else 0.0,
                    'mean_abs_inventory': round(statistics.mean(abs(s['inventory']) for s in m.samples), 6)})
    return row


def cell(base, spec, strategy, response, control, throttle):
    c, r = lookup(CONTROLS, control), lookup(RESPONSES, response)
    cfg = config_for(base, c, r, throttle)
    rows = [episode(cfg, s, c['gate'], strategy, spec['markout_seconds']) for s in spec['seeds']]
    return {'strategy': strategy, 'response': response, 'control': control, 'throttle': throttle, 'rows': rows}


def _cell_job(args):
    return cell(*args)


def summarize(cells, spec):
    by = {(c['strategy'], c['response'], c['control'], c['throttle']): c['rows'] for c in cells}
    out = []
    for c in cells:
        base_rows = by[(c['strategy'], c['response'], 'none', False)]
        label = f"{spec['bootstrap_seed']}|{c['strategy']}|{c['response']}|{c['control']}|{c['throttle']}"
        entry = {k: c[k] for k in ('strategy', 'response', 'control', 'throttle')}
        for measure in MEASURES[kind(c['strategy'])]:
            vals = [float(r[measure]) for r in c['rows']]
            entry[measure] = {'mean': round(statistics.mean(vals), 6),
                              'ci95': bootstrap(vals, spec['bootstrap_resamples'], f'{label}|{measure}')}
            if (c['control'], c['throttle']) != ('none', False):
                diff = [a - float(b[measure]) for a, b in zip(vals, base_rows)]
                entry[measure]['diff_vs_ungated'] = round(statistics.mean(diff), 6)
                entry[measure]['diff_ci95'] = bootstrap(diff, spec['bootstrap_resamples'], f'{label}|{measure}|diff')
        out.append(entry)
    return out


def jobs(base, spec):
    return [(base, spec, s, r, c, t) for s in spec['strategies'] for r in spec['responses']
            for c, t in [(a['control'], a['throttle']) for a in spec['arms']]]


def source_hashes():
    paths = [Path(__file__), Path(__file__).with_name('adversary.py'), Path(__file__).with_name('sensitivity.py'),
             BASE] + sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'cells': len(jobs(payload['config'], SPEC)), 'episodes': len(jobs(payload['config'], SPEC)) * len(SPEC['seeds'])}))


def compute(p, workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_cell_job, jobs(p['config'], p['spec'])))


def run(out, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    cells = compute(p, workers)
    with (out / 'episodes.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for c in cells:
            stream.write((canonical(c) + '\n').encode())
    summary = {'cells': summarize(cells, p['spec']), 'llm_episodes': 0,
               'inference_scope': 'Bootstrap 95% intervals over 100 market seeds for fixed scripts; '
                                  'differences are paired with the ungated arm on the same seed.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'cells': len(cells), 'episodes': sum(len(c['rows']) for c in cells)}))


def verify(out, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    cells = compute(p, workers)
    with gzip.open(out / 'episodes.jsonl.gz', 'rt') as stream:
        assert [json.loads(line) for line in stream] == json.loads(json.dumps(cells))
    assert summarize(cells, p['spec']) == json.loads((out / 'summary.json').read_text())['cells']
    result = {'cells_rerun': len(cells), 'episodes_rerun': sum(len(c['rows']) for c in cells),
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
