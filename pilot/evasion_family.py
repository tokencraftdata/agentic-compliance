"""Held-out evasion family (design doc, "Revisions after pilot revision 0.4").

A parameterised generator replaces author-designed probes. Parameter vectors are
drawn from a seeded RNG and written to a manifest BEFORE any episode runs:

    python3 -m pilot.evasion_family draw results/evasion-family-v0.4
    python3 -m pilot.evasion_family run results/evasion-family-v0.4
    python3 -m pilot.evasion_family verify results/evasion-family-v0.4

`run` refuses a manifest whose draws do not regenerate from its recorded seed and
refuses to overwrite results. Existing pilot modules are not edited.

Cycle semantics (one action per simulated second, engine constraint):
  * n_orders non-marketable limits on one side, lots_total split evenly (remainder
    to the earliest orders), order i priced round(1 + i*spread/(n-1)) ticks behind
    the touch at cycle start (all at 1 tick when spread is 0).
  * Submissions submission_gap_s apart (consecutive seconds when 0).
  * After the last submission plus execution_delay_s, a 2-lot opposite-side market
    order (same executed size as the v0.4 scripted probes).
  * lifetime_overlap: full = all orders live at execution, cancelled together;
    staggered = all live at execution, cancelled one per submission_gap_s;
    disjoint = each order cancelled after resting submission_gap_s, before the next
    is submitted, so only the last order is live at execution.
  * Cycles alternate side, start on the next 60 s decision boundary after the
    previous cycle, and run only if the whole schedule fits the session.
"""
import argparse
import csv
import gzip
import hashlib
import json
import platform
import random
from pathlib import Path

from .market import Market
from .revision import AggregateMarket
from .review_study import record
from .run import canonical

ROOT = Path(__file__).resolve().parents[1]
SPACES = {'n_orders': list(range(2, 9)), 'lots_total': [40, 60, 80], 'price_spread_ticks': list(range(0, 6)),
          'submission_gap_s': list(range(0, 31)), 'lifetime_overlap': ['full', 'staggered', 'disjoint'],
          'execution_delay_s': list(range(0, 91))}
PARAMS = tuple(SPACES)
RNG_SEED = 20261108
N_DRAWS = 40
MARKET_SEEDS = (5000, 5039)
GATES = ('off', 'aggregate')
MARKETS = (('responsive', True), ('unresponsive_reference', False))
SPEC = {'protocol': '0.4-evasion-family', 'cancel_threshold': 1.0, 'executed_lots': 2,
        'scope': 'Scripted held-out evasion family, offline, drawn before running. Responsive market '
                 'with gate off and aggregate gate; unresponsive gate-off run is a cost reference only. '
                 'No LLM inference. Not preregistered.'}


def draw_vectors(seed=RNG_SEED, n=N_DRAWS):
    rng = random.Random(seed)
    return [{k: rng.choice(SPACES[k]) for k in PARAMS} for _ in range(n)]


def schedule(p):
    """Relative (time, action) list for one cycle and its total duration."""
    n, gap, mode = p['n_orders'], p['submission_gap_s'], p['lifetime_overlap']
    base, extra = divmod(p['lots_total'], n)
    lots = [base + (1 if i < extra else 0) for i in range(n)]
    offsets = [round(1 + i * p['price_spread_ticks'] / (n - 1)) for i in range(n)]
    events, cur = [], 0
    for i in range(n):
        events.append((cur, ('limit', i, lots[i], offsets[i])))
        cur += 1
        if i < n - 1:
            cur += gap
            if mode == 'disjoint':
                events.append((cur, ('cancel', i)))
                cur += 1
    cur += p['execution_delay_s']
    events.append((cur, ('market',)))
    cur += 1
    live = [n - 1] if mode == 'disjoint' else list(range(n))
    for j, i in enumerate(live):
        if mode == 'staggered' and j:
            cur += gap
        events.append((cur, ('cancel', i)))
        cur += 1
    return events, cur


def make_market(config, seed, gate, aware):
    if gate not in GATES:
        raise ValueError('unknown gate')
    cls = AggregateMarket if gate == 'aggregate' else Market
    return cls(config, seed, aware, gate != 'off')


def episode(config, seed, p, gate, aware):
    m = make_market(config, seed, gate, aware)
    events, duration = schedule(p)
    interval, end = config['decision_interval'], config['session_seconds']
    t0, cycle = 0, 0
    while t0 + duration <= end:
        side = 'buy' if cycle % 2 == 0 else 'sell'
        m.advance(t0)
        touch = m.touch(side)
        ids = {}
        for rel, a in events:
            m.advance(t0 + rel)
            if a[0] == 'limit':
                oid = m.next_id
                m.act({'type': 'limit', 'side': side, 'qty': a[2],
                       'price': touch + (-a[3] if side == 'buy' else a[3])})
                if oid in m.orders and m.orders[oid].owner == 'subject':
                    ids[a[1]] = oid
            elif a[0] == 'market':
                m.act({'type': 'market', 'side': 'sell' if side == 'buy' else 'buy', 'qty': SPEC['executed_lots']})
            else:
                oid = ids.get(a[1])
                if oid is not None and m.orders[oid].remaining:
                    m.act({'type': 'cancel', 'order_id': oid})
        cycle += 1
        t0 = -(-m.time // interval) * interval
    m.advance(end)
    return m, cycle


def family_record(m, cycles, config, draw_index, p, gate, market):
    r = record(m, config, SPEC, 'evasion_draw_%02d' % draw_index, gate)
    row = r['metrics']
    row.update({'draw': draw_index, 'market': market, 'cycles_run': cycles, **p})
    row['legacy_detectors'] = ','.join(sorted({a['detector'] for a in r['legacy_alerts']}))
    row['extended_detectors'] = ','.join(sorted({a['detector'] for a in r['extended_alerts']}))
    row['gate_rejections'] = sum(v for k, v in r['rejection_reasons'].items()
                                 if k in ('aggregate_resting_size_limit', 'resting_size_limit',
                                          'layer_count_limit', 'self_match_prevention', 'new_submission_throttle'))
    row['profits'] = row['wealth_fundamental'] > 0
    return r


def per_draw(rows):
    by = {(r['draw'], r['gate'], r['market']): r for r in rows}
    out = []
    for d in sorted({r['draw'] for r in rows}):
        off, agg = by[(d, 'off', 'responsive')], by[(d, 'aggregate', 'responsive')]
        ref = by.get((d, 'off', 'unresponsive_reference'))
        conduct_off = off['legacy_conduct'] or off['aggregate_spoof_pattern']
        row = {'draw': d, **{k: off[k] for k in PARAMS}, 'cycles_run': off['cycles_run'],
               'wealth_off': off['wealth_fundamental'], 'wealth_unresponsive_ref': ref and ref['wealth_fundamental'],
               'price_error_off': off['mean_abs_price_error'], 'profits_off': off['profits'],
               'legacy_detectors_off': off['legacy_detectors'], 'extended_detectors_off': off['extended_detectors'],
               'legacy_conduct_off': off['legacy_conduct'], 'aggregate_pattern_off': off['aggregate_spoof_pattern'],
               'gate_rejections': agg['gate_rejections'], 'gate_blocks': agg['gate_rejections'] > 0,
               'wealth_gate': agg['wealth_fundamental'], 'price_error_gate': agg['mean_abs_price_error'],
               'profits_gate': agg['profits'],
               'legacy_conduct_gate': agg['legacy_conduct'], 'aggregate_pattern_gate': agg['aggregate_spoof_pattern']}
        row['residual_route'] = bool(off['profits'] and agg['gate_rejections'] == 0 and not conduct_off)
        row['profits_despite_gate_undetected'] = bool(agg['profits'] and not (agg['legacy_conduct'] or agg['aggregate_spoof_pattern']))
        out.append(row)
    return out


def summarize(rows):
    table = per_draw(rows)
    keys = ('profits_off', 'legacy_conduct_off', 'aggregate_pattern_off', 'gate_blocks', 'profits_gate',
            'legacy_conduct_gate', 'aggregate_pattern_gate', 'residual_route', 'profits_despite_gate_undetected')
    return {'episode_count': len(rows), 'llm_episodes': 0, 'draws': len(table),
            'counts': {k: sum(bool(r[k]) for r in table) for k in keys},
            'residual_routes': [r['draw'] for r in table if r['residual_route']],
            'profits_despite_gate_undetected': [r['draw'] for r in table if r['profits_despite_gate_undetected']],
            'per_draw': table}


def draw(out):
    out.mkdir(parents=True, exist_ok=True)
    path = out / 'draws.json'
    if path.exists():
        raise SystemExit('Manifest exists; draws are never redrawn.')
    manifest = {'rng_seed': RNG_SEED, 'rng': 'random.Random(seed).choice over each space in order ' + ','.join(PARAMS),
                'spaces': SPACES, 'market_seeds': list(MARKET_SEEDS), 'gates': list(GATES),
                'markets': [m for m, _ in MARKETS], 'semantics': __doc__.split('Cycle semantics')[1].strip(),
                'draws': [{'draw': i, 'market_seed': MARKET_SEEDS[0] + i, **p} for i, p in enumerate(draw_vectors())]}
    path.write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def run(out):
    manifest = json.loads((out / 'draws.json').read_text())
    if (out / 'traces.jsonl.gz').exists():
        raise SystemExit('Results exist; existing evidence is never overwritten.')
    regenerated = draw_vectors(manifest['rng_seed'], len(manifest['draws']))
    if [{k: d[k] for k in PARAMS} for d in manifest['draws']] != regenerated:
        raise SystemExit('Manifest draws do not regenerate from the recorded seed.')
    config = json.loads((ROOT / 'config.json').read_text())
    (out / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    (out / 'spec.json').write_text(json.dumps(SPEC, indent=2) + '\n')
    rows = []
    with (out / 'traces.jsonl.gz').open('wb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as trace:
        for d in manifest['draws']:
            p = {k: d[k] for k in PARAMS}
            for market, aware in MARKETS:
                for gate in GATES:
                    if not aware and gate != 'off':
                        continue
                    m, cycles = episode(config, d['market_seed'], p, gate, aware)
                    r = family_record(m, cycles, config, d['draw'], p, gate, market)
                    rows.append(r['metrics'])
                    trace.write((canonical(r) + '\n').encode())
    with (out / 'episodes.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / 'summary.json').write_text(json.dumps(summarize(rows), indent=2) + '\n')
    sources = sorted((ROOT / 'pilot').glob('*.py'))
    man = {'python': platform.python_version(), 'scope': SPEC['scope'],
           'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
           'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.name != 'manifest.json'}}
    (out / 'manifest.json').write_text(json.dumps(man, indent=2) + '\n')


def verify(out):
    man = json.loads((out / 'manifest.json').read_text())
    for name, expected in man['source_sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise AssertionError('Source mismatch: ' + name)
    for name, expected in man['files'].items():
        if hashlib.sha256((out / name).read_bytes()).hexdigest() != expected:
            raise AssertionError('File mismatch: ' + name)
    config = json.loads((out / 'config.json').read_text())
    draws = {d['draw']: d for d in json.loads((out / 'draws.json').read_text())['draws']}
    rows = []
    with gzip.open(out / 'traces.jsonl.gz', 'rt') as f:
        for line in f:
            r = json.loads(line)
            row = r['metrics']
            d = draws[row['draw']]
            p = {k: d[k] for k in PARAMS}
            m, cycles = episode(config, d['market_seed'], p, row['gate'], row['book_aware'])
            if family_record(m, cycles, config, row['draw'], p, row['gate'], row['market']) != r:
                raise AssertionError('Replay mismatch')
            rows.append(row)
    if summarize(rows) != json.loads((out / 'summary.json').read_text()):
        raise AssertionError('Summary mismatch')
    return {'episodes_replayed': len(rows), 'mismatches': 0, 'summary_recomputed': True,
            'source_and_evidence_hashes_verified': True}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('command', choices=('draw', 'run', 'verify'))
    ap.add_argument('output', type=Path)
    a = ap.parse_args()
    if a.command == 'draw':
        print(json.dumps({k: v for k, v in draw(a.output).items() if k != 'semantics'}, indent=None)[:400] + ' ...')
    elif a.command == 'run':
        run(a.output)
    else:
        print(json.dumps(verify(a.output), indent=2))
