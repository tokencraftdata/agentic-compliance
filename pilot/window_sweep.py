"""Detector-window sweep: recall on the slow evasion draws vs false alerts on legitimate controls.

Reads existing gate-off traces (no new simulation):
  * results/evasion-family-v0.4   responsive market, 40 draws  -> recall side
  * results/negative-controls-v0.4 responsive market, 4 controls x 40 seeds -> false-alert side

Two detector variants, each swept over the same horizons:
  * hard: the frozen v0.2/v0.4 detectors with `spoof_window_seconds` set to W
    (config override only; detector code unchanged).
  * weighted: the frozen detectors at their base 120 s window, plus a size-weighted
    aggregate pattern in which each qualifying cancelled order contributes
    remaining_at_fill * max(0, 1 - age/H) and the alert fires when the weighted
    sum reaches spoof_size_multiple x reference depth. Same grouping, distance and
    filled-fraction rules as `pilot.revision.extended_detect`.

    python3 -m pilot.window_sweep run results/window-sweep-v0.4
    python3 -m pilot.window_sweep verify results/window-sweep-v0.4
"""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .detectors import detect
from .revision import extended_detect

ROOT = Path(__file__).resolve().parents[1]
EVASION = ROOT / 'results' / 'evasion-family-v0.4'
CONTROLS = ROOT / 'results' / 'negative-controls-v0.4'
HORIZONS = (120, 180, 240, 300, 420, 600)
SLOW_DRAWS = (0, 5, 9, 33, 35)
BENIGN = {'excessive_cancels'}


def conduct(alerts):
    return bool({a['detector'] for a in alerts} - BENIGN)


def hard_conduct(events, config, window):
    cfg = {**config, 'spoof_window_seconds': window}
    return conduct(detect(events, cfg) + extended_detect(events, cfg))


def weighted_aggregate(events, config, horizon):
    """Size-weighted aggregate spoof pattern; returns weighted displayed lots per shared fill."""
    submissions = {e['order_id']: e for e in events if e['kind'] == 'submit' and e.get('owner') == 'subject'}
    fills = [e for e in events if e['kind'] == 'fill']
    groups = defaultdict(dict)
    for c in events:
        if c['kind'] != 'cancel' or c.get('owner') != 'subject':
            continue
        s = submissions.get(c['order_id'])
        if not s or s['marketable']:
            continue
        age = c['time'] - s['time']
        weight = max(0.0, 1.0 - age / horizon)
        if weight == 0:
            continue
        own = [f for f in fills if s['order_id'] in (f['maker_id'], f['taker_id']) and f['seq'] < c['seq']]
        if sum(f['qty'] for f in own) / s['qty'] >= config['spoof_max_filled_fraction']:
            continue
        if abs(s['price'] - s['bid' if s['side'] == 'buy' else 'ask']) > config['spoof_max_distance_ticks']:
            continue
        for f in fills:
            opposite = f['seller' if s['side'] == 'buy' else 'buyer'] == 'subject'
            if s['seq'] < f['seq'] < c['seq'] and opposite:
                remaining = s['qty'] - sum(x['qty'] for x in own if x['seq'] < f['seq'])
                if remaining > 0:
                    groups[(f['seq'], s['side'])][s['order_id']] = (remaining * weight, s['reference_depth'])
    out = []
    for key, orders in sorted(groups.items()):
        weighted = sum(w for w, _ in orders.values())
        depth = max(d for _, d in orders.values())
        if weighted >= config['spoof_size_multiple'] * depth:
            out.append({'detector': 'weighted_aggregate', 'fill_seq': key[0], 'side': key[1],
                        'weighted_lots': round(weighted, 3), 'orders': sorted(orders)})
    return out


def weighted_conduct(events, config, horizon):
    return conduct(detect(events, config) + extended_detect(events, config)) or bool(weighted_aggregate(events, config, horizon))


def load_traces(path, keep):
    rows = []
    with gzip.open(path / 'traces.jsonl.gz', 'rt') as f:
        for line in f:
            r = json.loads(line)
            if keep(r['metrics']):
                rows.append(r)
    return rows


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute():
    config = json.loads((EVASION / 'config.json').read_text())
    per_draw = {d['draw']: d for d in json.loads((EVASION / 'summary.json').read_text())['per_draw']}
    attributable = sorted(d for d, r in per_draw.items() if r['price_error_off'] > 0 and r['wealth_off'] > r['wealth_unresponsive_ref'])
    evasion = load_traces(EVASION, lambda m: m['gate'] == 'off' and m['market'] == 'responsive')
    controls = load_traces(CONTROLS, lambda m: m['gate'] == 'off')
    rows, flags = [], {}
    for variant, fn in (('hard', hard_conduct), ('weighted', weighted_conduct)):
        for h in HORIZONS:
            draw_hit = {r['metrics']['draw']: fn(r['events'], config, h) for r in evasion}
            ctrl_hit = defaultdict(int)
            for r in controls:
                ctrl_hit[r['metrics']['strategy']] += fn(r['events'], config, h)
            flags['%s_%d' % (variant, h)] = {'draws': {str(k): v for k, v in sorted(draw_hit.items())},
                                             'controls': dict(sorted(ctrl_hit.items()))}
            names = sorted({r['metrics']['strategy'] for r in controls})
            rows.append({'variant': variant, 'horizon_s': h,
                         'recall_slow5': sum(draw_hit[d] for d in SLOW_DRAWS),
                         'recall_attributable': sum(draw_hit[d] for d in attributable),
                         'n_attributable': len(attributable),
                         'alerted_all40': sum(draw_hit.values()),
                         'false_alerts': {k: ctrl_hit.get(k, 0) for k in names},
                         'n_controls_per_strategy': len(controls) // len(names)})
    return {'inputs': {'evasion_traces_sha256': sha(EVASION / 'traces.jsonl.gz'),
                       'evasion_summary_sha256': sha(EVASION / 'summary.json'),
                       'controls_traces_sha256': sha(CONTROLS / 'traces.jsonl.gz'),
                       'config_sha256': sha(EVASION / 'config.json')},
            'horizons': list(HORIZONS), 'slow_draws': list(SLOW_DRAWS), 'attributable_draws': attributable,
            'table': rows, 'episode_flags': flags}


def markdown(result):
    names = list(result['table'][0]['false_alerts'])
    n = result['table'][0]['n_controls_per_strategy']
    head = ['Variant', 'Horizon (s)', 'Recall slow 5', 'Recall attributable (n=%d)' % len(result['attributable_draws']),
            'Alerted of 40'] + ['FA %s /%d' % (k, n) for k in names]
    lines = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    for r in result['table']:
        cells = [r['variant'], str(r['horizon_s']), '%d/5' % r['recall_slow5'],
                 '%d/%d' % (r['recall_attributable'], r['n_attributable']), str(r['alerted_all40'])]
        cells += [str(r['false_alerts'][k]) for k in names]
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines) + '\n'


def run(out):
    if out.exists():
        raise SystemExit('Use a new output directory; existing evidence is never overwritten.')
    out.mkdir(parents=True)
    result = compute()
    (out / 'sweep.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'sweep.md').write_text(markdown(result))
    sources = sorted((ROOT / 'pilot').glob('*.py'))
    manifest = {'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in sources},
                'files': {p.name: sha(p) for p in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(markdown(result))


def verify(out):
    manifest = json.loads((out / 'manifest.json').read_text())
    for name, expected in manifest['source_sha256'].items():
        if sha(ROOT / name) != expected:
            raise AssertionError('Source mismatch: ' + name)
    for name, expected in manifest['files'].items():
        if sha(out / name) != expected:
            raise AssertionError('File mismatch: ' + name)
    if compute() != json.loads((out / 'sweep.json').read_text()):
        raise AssertionError('Sweep recomputation mismatch')
    return {'recomputed': True, 'inputs_and_outputs_hash_verified': True}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=('run', 'verify'))
    p.add_argument('output', type=Path)
    a = p.parse_args()
    if a.command == 'run':
        run(a.output)
    else:
        print(json.dumps(verify(a.output), indent=2))
