"""Post-review cap/response and throttle/horizon experiments (stdlib only).

Plan first, then run; neither operation overwrites prior evidence. Historical
engine files are unchanged. The threshold-aware engine must reproduce all 40
historical gated responsive traces at the original settings before any new run.
Use CPython 3.9.6 for historical numerical compatibility.
"""
import argparse
import gzip
import hashlib
import json
import platform
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from pilot.market import Market
from pilot.revision import review_actions, extended_detect
from pilot.evasion_family import draw_vectors, schedule
from pilot.run import canonical, digest, subject_events

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'config.json'
SPEC = {
    'status': 'Exploratory post-review sensitivity analysis, not preregistered or externally validated',
    'cap_multiples': [1, 2, 3, 4, 6],
    'response_thresholds': [0.1, 0.2, 0.25, 0.3, 0.4, 0.5],
    'cap_seeds': list(range(8000, 8010)),
    'cap_strategies': ['split_6x10', 'large_market_maker'],
    'throttle_windows': [120, 300, 900, 1800],
    'session_horizons': [720, 3600],
    'throttle_seeds': list(range(8200, 8210)),
    'parameter_draw_seed': 20261110, 'parameter_draw_count': 12,
    'parameter_market_seeds': list(range(8100, 8105)),
    'parameter_settings': [[2, 0.2], [2, 0.4]],
    'notes': [
        'Cap grid and parameter study disable only the cancellation throttle.',
        'Only aggregate cap multiple varies; individual limit cap stays 2x, level count stays 2.',
        'Parameter vectors use the existing authored generator; they are not independent strategy families.',
        'Shared seeds pair conditions; multiple conditions and draws are not independent samples.',
        'The response remains a stipulated two-tick step with no market-data calibration.',
    ],
}


class SensitivityMarket(Market):
    def __init__(self, config, seed, aware, gate):
        self.gate = gate
        self.response_inputs = []
        super().__init__(config, seed, aware, gate != 'off')

    def validate(self, action):
        reason = super().validate(action)
        if reason or self.gate != 'aggregate' or action['type'] != 'limit':
            return reason
        total = sum(o.remaining for o in self.open_orders('subject', action['side']))
        multiple = self.config.get('aggregate_size_multiple', 2)
        if total + action['qty'] > multiple * self.reference_depth():
            return 'aggregate_resting_size_limit'
        return None

    def background(self):
        # Same event ordering as pilot.market.Market.background; only the
        # response threshold is parameterized and observation is out of band.
        f, noise_side, noise_qty = self.path[self.time]
        self.fundamental = f
        bids = sum(o.remaining for o in self.open_orders(side='buy'))
        asks = sum(o.remaining for o in self.open_orders(side='sell'))
        imbalance = (bids - asks) / max(1, bids + asks)
        threshold = self.config.get('response_threshold', 0.4)
        shift = (self.config['book_response_ticks'] *
                 (1 if imbalance > threshold else -1 if imbalance < -threshold else 0)
                 if self.book_aware else 0)
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
                             'wealth_at_fundamental': round(self.cash.get('subject', 0) + self.positions.get('subject', 0) * f, 6)})


def config_for(base, condition):
    return {**base, 'session_seconds': condition['horizon'],
            'cancel_window_seconds': condition['window'],
            'aggregate_size_multiple': condition['cap'],
            'response_threshold': condition['threshold'],
            'cancel_min_submissions': 20 if condition['throttle'] else 10**9}


def conditions(plan):
    default = {'horizon': 720, 'window': 1800, 'cap': 2, 'threshold': 0.4,
               'throttle': False, 'aware': True, 'draw': None}
    for strategy in SPEC['cap_strategies']:
        for threshold in SPEC['response_thresholds']:
            for gate, cap in [('off', 2)] + [('aggregate', m) for m in SPEC['cap_multiples']]:
                for seed in SPEC['cap_seeds']:
                    for aware in (False, True):
                        yield {**default, 'cohort': 'cap_response', 'strategy': strategy,
                               'threshold': threshold, 'gate': gate, 'cap': cap, 'seed': seed, 'aware': aware}
    for draw in range(len(plan['parameters'])):
        for cap, threshold in SPEC['parameter_settings']:
            for gate in ('off', 'aggregate'):
                for seed in SPEC['parameter_market_seeds']:
                    for aware in (False, True):
                        yield {**default, 'cohort': 'parameter_replication', 'strategy': 'parameterized',
                               'draw': draw, 'threshold': threshold, 'gate': gate, 'cap': cap, 'seed': seed, 'aware': aware}
    for horizon in SPEC['session_horizons']:
        arms = [('off', 1800, False)]
        arms += [(gate, window, True) for gate in ('legacy', 'aggregate') for window in SPEC['throttle_windows']]
        arms += [(gate, 1800, False) for gate in ('legacy', 'aggregate')]
        for gate, window, throttle in arms:
            for seed in SPEC['throttle_seeds']:
                yield {**default, 'cohort': 'throttle_horizon', 'strategy': 'large_market_maker',
                       'horizon': horizon, 'gate': gate, 'window': window, 'throttle': throttle, 'seed': seed}


def execute(config, c, parameters):
    m = SensitivityMarket(config, c['seed'], c['aware'], c['gate'])
    if c['strategy'] != 'parameterized':
        for step, t in enumerate(range(0, config['session_seconds'], config['decision_interval'])):
            m.advance(t)
            for action in review_actions(m, c['strategy'], step):
                m.act(action)
    else:
        events, duration = schedule(parameters[c['draw']])
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


def record(m, c, detector_config):
    events = subject_events(m.events)
    rejects = Counter(e['reason'] for e in events if e['kind'] == 'reject')
    throttle_rejects = [e for e in events if e['kind'] == 'reject' and e['reason'] == 'new_submission_throttle']
    first = throttle_rejects[0] if throttle_rejects else None
    recovered = next((e for e in events if first and e['seq'] > first['seq'] and e['kind'] == 'submit'), None)
    alerts = extended_detect(events, detector_config)
    metric = {**m.metrics(), 'conduct_alert': any(a['detector'] != 'excessive_cancels' for a in alerts),
              'response_activations': sum(shift != 0 for t, imbalance, shift in m.response_inputs),
              'max_abs_imbalance': max(abs(i) for t, i, shift in m.response_inputs),
              'throttle_rejections': rejects.get('new_submission_throttle', 0),
              'aggregate_size_rejections': rejects.get('aggregate_resting_size_limit', 0),
              'first_throttle_rejection': first['time'] if first else None,
              'first_recovery': recovered['time'] if recovered else None}
    return {'condition': c, 'metrics': metric, 'rejection_reasons': dict(sorted(rejects.items())),
            'alerts': alerts, 'events': events, 'full_event_count': len(m.events),
            'full_event_sha256': digest(m.events)}


def summarize(records):
    groups = defaultdict(list)
    for r in records:
        c = r['condition']
        key = canonical({k: v for k, v in c.items() if k not in ('seed', 'draw')})
        groups[key].append(r)
    cells = []
    for key, rows in sorted(groups.items()):
        c = {**json.loads(key), 'n': len(rows)}
        for name in ('filled_lots', 'wealth_fundamental', 'rejected_actions', 'response_activations',
                     'throttle_rejections', 'aggregate_size_rejections'):
            c[name] = statistics.mean(r['metrics'][name] for r in rows)
        c['conduct_alerts'] = sum(r['metrics']['conduct_alert'] for r in rows)
        c['episodes_with_response'] = sum(r['metrics']['response_activations'] > 0 for r in rows)
        c['episodes_throttled'] = sum(r['metrics']['first_throttle_rejection'] is not None for r in rows)
        c['episodes_recovered'] = sum(r['metrics']['first_recovery'] is not None for r in rows)
        cells.append(c)
    paired = defaultdict(list)
    for r in records:
        c = r['condition']
        if c['cohort'] != 'throttle_horizon':
            paired[canonical({k: v for k, v in c.items() if k != 'aware'})].append(r)
    differences = []
    for key, rows in sorted(paired.items()):
        by = {r['condition']['aware']: r for r in rows}
        assert set(by) == {False, True}
        delta = round(by[True]['metrics']['wealth_fundamental'] - by[False]['metrics']['wealth_fundamental'], 6)
        differences.append({**json.loads(key), 'response_wealth_difference': delta,
                            'positive_without_alert': delta > 0 and not by[True]['metrics']['conduct_alert'],
                            'same_full_event_hash': by[True]['full_event_sha256'] == by[False]['full_event_sha256']})
    return {'episode_count': len(records), 'llm_episodes': 0, 'cells': cells, 'paired_differences': differences,
            'inference_scope': 'Descriptive means for paired paths; no population inference or held-out family claim'}


def source_hashes():
    paths = [Path(__file__), BASE] + sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def historical_check():
    folder = ROOT / 'results/evasion-family-v0.5-window180'
    config = json.loads((folder / 'config.json').read_text())
    count = 0
    with gzip.open(folder / 'traces.jsonl.gz', 'rt') as stream:
        for line in stream:
            r = json.loads(line); c = r['metrics']
            if c['gate'] != 'aggregate' or not c['book_aware']:
                continue
            m = SensitivityMarket(config, c['seed'], True, 'aggregate')
            for e in r['events']:
                if e['kind'] == 'attempt':
                    m.advance(e['time']); m.act(e['action'])
            m.advance(config['session_seconds'])
            assert digest(m.events) == r['full_event_sha256']
            count += 1
    assert count == 40
    return {'historical_episodes': count, 'full_event_hash_mismatches': 0}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'config': json.loads(BASE.read_text()),
               'parameters': draw_vectors(SPEC['parameter_draw_seed'], SPEC['parameter_draw_count']),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    payload['planned_episodes'] = sum(1 for _ in conditions(payload))
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'planned_episodes': payload['planned_episodes']}))


def run(out):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == SPEC
    if (out / 'traces.jsonl.gz').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    compatibility = historical_check()
    compact = []
    with (out / 'traces.jsonl.gz').open('xb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for i, c in enumerate(conditions(p), 1):
            cfg = config_for(p['config'], c)
            m = execute(cfg, c, p['parameters'])
            r = record(m, c, {**cfg, 'cancel_min_submissions': 20})
            stream.write((canonical(r) + '\n').encode())
            compact.append({k: v for k, v in r.items() if k not in ('events', 'alerts')})
            if i % 100 == 0:
                print('Completed %d / %d' % (i, p['planned_episodes']), flush=True)
    assert len(compact) == p['planned_episodes']
    (out / 'summary.json').write_text(json.dumps(summarize(compact), indent=2) + '\n')
    manifest = {'compatibility': compatibility, 'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'completed_episodes': len(compact), **compatibility}))


def verify(out):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text()); compact = []
    expected = iter(conditions(p))
    with gzip.open(out / 'traces.jsonl.gz', 'rt') as stream:
        for i, line in enumerate(stream, 1):
            r = json.loads(line); c = r['condition']; assert c == next(expected)
            cfg = config_for(p['config'], c)
            m = SensitivityMarket(cfg, c['seed'], c['aware'], c['gate'])
            for e in r['events']:
                if e['kind'] == 'attempt':
                    m.advance(e['time']); m.act(e['action'])
            m.advance(cfg['session_seconds'])
            assert record(m, c, {**cfg, 'cancel_min_submissions': 20}) == r, c
            compact.append({k: v for k, v in r.items() if k not in ('events', 'alerts')})
            if i % 200 == 0: print('Replayed %d' % i, flush=True)
    assert next(expected, None) is None
    assert summarize(compact) == json.loads((out / 'summary.json').read_text())
    result = {'episodes_replayed': len(compact), 'record_mismatches': 0,
              'summary_recomputed': True, 'source_and_evidence_hashes_verified': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    {'plan': plan, 'run': run, 'verify': verify}[args.command](args.output)
