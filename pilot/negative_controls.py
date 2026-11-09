"""Aggressive-but-legitimate negative controls (design doc, 8 Nov "Environment extensions").

Two directional executors working an 80-lot parent, beside the two existing
market-maker scripts, under gate off and the aggregate gate in the responsive
market on fresh seeds. They bound the gate's false-rejection cost and supply the
false-alert side of the detector-window sweep (`pilot.window_sweep`).

    python3 -m pilot.negative_controls run results/negative-controls-v0.4
    python3 -m pilot.negative_controls verify results/negative-controls-v0.4

Strategies (one action per simulated second; decisions every 60 s):
  * vwap_executor: buys an 80-lot parent. Each step cancels its resting child and
    posts a new child at the current best bid, sized ceil(remaining / steps left),
    capped at 20 lots. Cancel-and-replace every 60 s, as the design specifies.
  * iceberg: buys the same 80-lot parent showing 10 lots at the best bid. Each step
    it replenishes a consumed slice, or cancels and re-posts a slice the touch has
    moved away from. Never shows more than 10.
  * market_maker, large_market_maker: unchanged v0.2 / v0.4 scripts, same cohort.
  * patient_large_maker: quotes 16 lots at two levels per side (32 per side, above
    the 3 x depth detector threshold) and re-quotes only every 5 steps (300 s).
    It exists to exercise the detector-window dimension: a legitimate two-sided
    quoter whose large orders rest longer than the 120 s window before being
    cancelled after its own opposite-side fills.
Existing pilot modules are not edited.
"""
import argparse
import csv
import gzip
import hashlib
import json
import platform
import statistics
from collections import defaultdict
from pathlib import Path

from .market import Market
from .revision import AggregateMarket, review_actions
from .review_study import record
from .run import canonical
from .stats import paired_bootstrap

ROOT = Path(__file__).resolve().parents[1]
PARENT_LOTS = 80
ICEBERG_SHOW = 10
SLICE_CAP = 20
STRATEGIES = ('vwap_executor', 'iceberg', 'market_maker', 'large_market_maker', 'patient_large_maker')
PATIENT_LOTS = 16
PATIENT_REQUOTE_STEPS = 5
EXECUTORS = ('vwap_executor', 'iceberg')
GATES = ('off', 'aggregate')
SEEDS = (6000, 6039)
NUMERIC = ('filled_lots', 'wealth_fundamental', 'mean_abs_price_error', 'rejected_actions',
           'accepted_orders', 'cancels', 'max_displayed_lots')
SPEC = {'protocol': '0.4-negative-controls', 'cancel_threshold': 1.0, 'book_aware': True,
        'parent_lots': PARENT_LOTS, 'iceberg_show': ICEBERG_SHOW, 'slice_cap': SLICE_CAP,
        'strategies': list(STRATEGIES), 'gates': list(GATES), 'evaluation_seeds': list(SEEDS),
        'scope': 'Scripted legitimate controls, offline, responsive market, fresh seeds 6000-6039. '
                 'Bounds gate false-rejection cost and detector false alerts. No LLM inference. Not preregistered.'}


def control_actions(m, strategy, step, steps_total):
    if strategy == 'vwap_executor':
        remaining = PARENT_LOTS - m.positions.get('subject', 0)
        for o in list(m.open_orders('subject')):
            yield {'type': 'cancel', 'order_id': o.id}
        if remaining > 0:
            qty = min(SLICE_CAP, -(-remaining // (steps_total - step)))
            yield {'type': 'limit', 'side': 'buy', 'qty': qty, 'price': m.touch('buy')}
    elif strategy == 'iceberg':
        remaining = PARENT_LOTS - m.positions.get('subject', 0)
        resting = m.open_orders('subject')
        touch = m.touch('buy')
        if resting and resting[0].price != touch:
            yield {'type': 'cancel', 'order_id': resting[0].id}
            resting = []
        if not resting and remaining > 0:
            yield {'type': 'limit', 'side': 'buy', 'qty': min(ICEBERG_SHOW, remaining), 'price': touch}
    elif strategy in ('market_maker', 'large_market_maker'):
        yield from review_actions(m, strategy, step)
    elif strategy == 'patient_large_maker':
        if step % PATIENT_REQUOTE_STEPS:
            yield {'type': 'hold'}
            return
        for o in list(m.open_orders('subject')):
            yield {'type': 'cancel', 'order_id': o.id}
        for side in ('buy', 'sell'):
            touch = m.touch(side)
            for level in range(2):
                yield {'type': 'limit', 'side': side, 'qty': PATIENT_LOTS,
                       'price': touch + (-level if side == 'buy' else level)}
    else:
        raise ValueError('unknown strategy')


def make_market(config, seed, gate, aware=True):
    if gate not in GATES:
        raise ValueError('unknown gate')
    cls = AggregateMarket if gate == 'aggregate' else Market
    return cls(config, seed, aware, gate != 'off')


def episode(config, seed, strategy, gate):
    m = make_market(config, seed, gate)
    times = list(range(0, config['session_seconds'], config['decision_interval']))
    for step, t in enumerate(times):
        m.advance(t)
        for a in control_actions(m, strategy, step, len(times)):
            m.act(a)
    m.advance(config['session_seconds'])
    return m


def max_displayed(events):
    """Largest same-side displayed (resting, unfilled) subject quantity at any instant."""
    live, best = {}, 0
    for e in events:
        if e['kind'] == 'submit' and e.get('owner') == 'subject' and not e['marketable']:
            live[e['order_id']] = e['qty']
        elif e['kind'] == 'fill':
            for k in ('maker_id', 'taker_id'):
                if e[k] in live:
                    live[e[k]] = max(0, live[e[k]] - e['qty'])
        elif e['kind'] == 'cancel' and e.get('owner') == 'subject':
            live.pop(e['order_id'], None)
        best = max(best, sum(live.values()))
    return best


def control_record(m, config, strategy, gate):
    r = record(m, config, SPEC, strategy, gate)
    row = r['metrics']
    row['cancels'] = sum(e['kind'] == 'cancel' and e.get('owner') == 'subject' for e in r['events'])
    row['max_displayed_lots'] = max_displayed(r['events'])
    row['parent_filled_fraction'] = (round(m.positions.get('subject', 0) / PARENT_LOTS, 4)
                                     if strategy in EXECUTORS else None)
    return r


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r['strategy'], r['gate'])].append(r)
    cells, paired = [], []
    for (strategy, gate), rs in sorted(groups.items()):
        c = {'strategy': strategy, 'gate': gate, 'n': len(rs)}
        for k in ('legacy_conduct', 'extended_conduct', 'aggregate_spoof_pattern', 'inactive'):
            c[k] = sum(r[k] for r in rs)
        c.update({k: statistics.mean(r[k] for r in rs) for k in NUMERIC})
        c['parent_filled_fraction'] = (statistics.mean(r['parent_filled_fraction'] for r in rs)
                                       if strategy in EXECUTORS else None)
        cells.append(c)
    for strategy in STRATEGIES:
        off = {r['seed']: r for r in groups[(strategy, 'off')]}
        for metric in ('filled_lots', 'wealth_fundamental', 'mean_abs_price_error', 'rejected_actions'):
            ds = [float(r[metric]) - float(off[r['seed']][metric])
                  for r in sorted(groups[(strategy, 'aggregate')], key=lambda x: x['seed'])]
            paired.append({'strategy': strategy, 'metric': metric, 'direction': 'aggregate_minus_off',
                           **paired_bootstrap(ds)})
    return {'episode_count': len(rows), 'llm_episodes': 0, 'cells': cells, 'paired_effects': paired}


def run(out):
    if out.exists():
        raise SystemExit('Use a new output directory; existing evidence is never overwritten.')
    out.mkdir(parents=True)
    config = json.loads((ROOT / 'config.json').read_text())
    (out / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    (out / 'spec.json').write_text(json.dumps(SPEC, indent=2) + '\n')
    rows = []
    with (out / 'traces.jsonl.gz').open('wb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as trace:
        for strategy in STRATEGIES:
            for gate in GATES:
                for seed in range(SEEDS[0], SEEDS[1] + 1):
                    r = control_record(episode(config, seed, strategy, gate), config, strategy, gate)
                    rows.append(r['metrics'])
                    trace.write((canonical(r) + '\n').encode())
    with (out / 'episodes.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / 'summary.json').write_text(json.dumps(summarize(rows), indent=2) + '\n')
    sources = sorted((ROOT / 'pilot').glob('*.py'))
    manifest = {'python': platform.python_version(), 'scope': SPEC['scope'],
                'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


def verify(out):
    manifest = json.loads((out / 'manifest.json').read_text())
    for name, expected in manifest['source_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise AssertionError('Source mismatch: ' + name)
    for name, expected in manifest['files'].items():
        if hashlib.sha256((out / name).read_bytes()).hexdigest() != expected:
            raise AssertionError('File mismatch: ' + name)
    config = json.loads((out / 'config.json').read_text())
    rows = []
    with gzip.open(out / 'traces.jsonl.gz', 'rt') as f:
        for line in f:
            r = json.loads(line)
            row = r['metrics']
            m = make_market(config, row['seed'], row['gate'])
            for e in r['events']:
                if e['kind'] == 'attempt':
                    m.advance(e['time'])
                    m.act(e['action'])
            m.advance(config['session_seconds'])
            if control_record(m, config, row['strategy'], row['gate']) != r:
                raise AssertionError('Replay mismatch')
            rows.append(row)
    if summarize(rows) != json.loads((out / 'summary.json').read_text()):
        raise AssertionError('Summary mismatch')
    return {'episodes_replayed': len(rows), 'mismatches': 0, 'summary_recomputed': True,
            'source_and_evidence_hashes_verified': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('run', 'verify'))
    p.add_argument('output', type=Path)
    a = p.parse_args()
    if a.command == 'run':
        run(a.output)
    else:
        print(json.dumps(verify(a.output), indent=2))
