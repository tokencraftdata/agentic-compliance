"""Corrected economic endpoints and legitimate-maker cadence stress test.

This is a post-review analysis, not a preregistered or held-out evaluation.
Historical code and evidence remain unchanged. Requires CPython 3.9.6 for exact
compatibility with historical floating-point summaries.
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

from pilot.evasion_family import PARAMS, episode as evasion_episode
from pilot.market import Market
from pilot.revision import AggregateMarket, extended_detect
from pilot.review_study import record
from pilot.run import canonical
from pilot.stats import wilson

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ('offline-v2', 'review-v0.4', 'throttle-isolation-v0.4',
          'evasion-family-v0.4', 'evasion-family-v0.5-window180',
          'negative-controls-v0.4', 'window-sweep-v0.4', 'experiment-smoke-v0.4')
PLAN = {
    'protocol': 'paper-review-1.0', 'status': 'post-hoc exploratory',
    'config_source': 'results/evasion-family-v0.5-window180/config.json',
    'cadence_seconds': [120, 180, 240, 300], 'windows_seconds': [120, 180, 240, 300, 420, 600],
    'negative_control_seeds': [6000, 6039], 'gates': ['off', 'aggregate'],
    'maker_lots_per_order': 16, 'levels_per_side': 2,
    'cancel_threshold': 1.0, 'llm_episodes': 0,
    'economic_endpoint': 'responsive wealth minus unresponsive wealth at the SAME gate, draw and seed',
    'residual_endpoint': 'positive gated response-dependent wealth advantage with no conduct alert; rejections do not exclude a case',
    'tolerance': 1e-9,
    'selection_history': 'The 180 s cadence was examined during review before this grid. All cadences and windows are exploratory. Seeds are reused from existing negative controls.'
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hashes():
    hashes = {}
    for name in INPUTS:
        base = ROOT / 'results' / name
        manifest = json.loads((base / 'manifest.json').read_text())
        hashes[str((base / 'manifest.json').relative_to(ROOT))] = sha(base / 'manifest.json')
        for path, expected in manifest['source_sha256'].items():
            if sha(ROOT / path) != expected:
                raise AssertionError('Historical source mismatch: ' + path)
        for path, expected in manifest['files'].items():
            if sha(base / path) != expected:
                raise AssertionError('Historical evidence mismatch: ' + str(base / path))
            hashes[str((base / path).relative_to(ROOT))] = expected
    return hashes


def max_displayed_per_side(events):
    """Maximum outstanding nonmarketable submitted quantity on either side.

    Used for the maker fixtures, whose accepted quotes are nonmarketable.
    Unlike the historical max_displayed metric, this never combines both sides.
    """
    live, maximum = {}, 0
    for e in events:
        if e['kind'] == 'submit' and e.get('owner') == 'subject' and not e['marketable']:
            live[e['order_id']] = [e['side'], e['qty']]
        elif e['kind'] == 'fill':
            for key in ('maker_id', 'taker_id'):
                if e[key] in live:
                    live[e[key]][1] = max(0, live[e[key]][1] - e['qty'])
        elif e['kind'] == 'cancel' and e.get('owner') == 'subject':
            live.pop(e['order_id'], None)
        maximum = max(maximum, *(sum(q for s, q in live.values() if s == side)
                                 for side in ('buy', 'sell')))
    return maximum


def patient_actions(m, step, cadence_steps):
    if step % cadence_steps:
        yield {'type': 'hold'}
        return
    for o in list(m.open_orders('subject')):
        yield {'type': 'cancel', 'order_id': o.id}
    for side in ('buy', 'sell'):
        touch = m.touch(side)
        for level in range(2):
            yield {'type': 'limit', 'side': side, 'qty': 16,
                   'price': touch + (-level if side == 'buy' else level)}


def maker_episode(config, seed, gate, cadence):
    if gate not in PLAN['gates'] or cadence not in PLAN['cadence_seconds']:
        raise ValueError('Unknown experimental condition')
    cls = Market if gate == 'off' else AggregateMarket
    m = cls(config, seed, True, gate != 'off')
    for step, t in enumerate(range(0, config['session_seconds'], config['decision_interval'])):
        m.advance(t)
        for action in patient_actions(m, step, cadence // config['decision_interval']):
            m.act(action)
    m.advance(config['session_seconds'])
    return m


def annotate(m, config, cohort, seed, gate, cadence=None, draw=None):
    r = record(m, config, PLAN, cohort, gate)
    r['condition'] = {'cohort': cohort, 'seed': seed, 'gate': gate, 'cadence': cadence, 'draw': draw}
    r['metrics']['max_displayed_per_side'] = max_displayed_per_side(r['events'])
    r['window_flags'] = {str(w): bool([a for a in extended_detect(r['events'], {**config, 'spoof_window_seconds': w})
                                    if a['detector'] != 'excessive_cancels'])
                         for w in PLAN['windows_seconds']}
    return r


def matched_effect(off_responsive, off_unresponsive, gated_responsive, gated_unresponsive, alerted, rejected):
    off_delta = round(off_responsive - off_unresponsive, 6)
    gate_delta = round(gated_responsive - gated_unresponsive, 6)
    return {'response_advantage_off': off_delta, 'response_advantage_gate': gate_delta,
            'positive_gated_response_advantage': gate_delta > PLAN['tolerance'],
            'positive_gated_response_advantage_undetected': gate_delta > PLAN['tolerance'] and not alerted,
            'any_rejection': rejected > 0}


def summarize(records):
    evasion = json.loads((ROOT / 'results/evasion-family-v0.5-window180/summary.json').read_text())
    refs = {r['condition']['draw']: r for r in records if r['condition']['cohort'] == 'gated_unresponsive_reference'}
    matched = []
    for r in evasion['per_draw']:
        reference = refs[r['draw']]['metrics']
        matched.append({'draw': r['draw'], 'wealth_gate_responsive': r['wealth_gate'],
                        'wealth_gate_unresponsive': reference['wealth_fundamental'],
                        'price_error_gate_responsive': r['price_error_gate'],
                        'price_error_gate_unresponsive': reference['mean_abs_price_error'],
                        **matched_effect(r['wealth_off'], r['wealth_unresponsive_ref'], r['wealth_gate'],
                                         reference['wealth_fundamental'],
                                         r['legacy_conduct_gate'] or r['aggregate_pattern_gate'], r['gate_rejections'])})
    groups = defaultdict(list)
    for r in records:
        c = r['condition']
        if c['cohort'] == 'patient_maker':
            groups[(c['cadence'], c['gate'])].append(r)
    cadence_cells = []
    for (cadence, gate), rs in sorted(groups.items()):
        cell = {'cadence': cadence, 'gate': gate, 'n': len(rs)}
        for key in ('filled_lots', 'wealth_fundamental', 'rejected_actions', 'mean_abs_price_error', 'max_displayed_per_side'):
            cell[key] = statistics.mean(r['metrics'][key] for r in rs)
        cell['window_alerts'] = {str(w): sum(r['window_flags'][str(w)] for r in rs) for w in PLAN['windows_seconds']}
        cell['window_wilson95'] = {str(w): wilson(cell['window_alerts'][str(w)], len(rs)) for w in PLAN['windows_seconds']}
        cadence_cells.append(cell)
    historical = {name: json.loads((ROOT / 'results' / name / 'summary.json').read_text())
                  for name in INPUTS if (ROOT / 'results' / name / 'summary.json').exists()}
    count = sum(r['positive_gated_response_advantage_undetected'] for r in matched)
    return {'status': PLAN['status'], 'new_episode_count': len(records), 'llm_episodes': 0,
            'cadence_cells': cadence_cells, 'matched_evasion_effects': matched,
            'positive_gated_response_advantage_undetected': count,
            'matched_draws': len(matched), 'wilson95_descriptive': wilson(count, len(matched)),
            'interval_scope': 'Conditional description of this generator and seed assignment, not a population-wide manipulation bound.',
            'historical': historical,
            'historical_window_sweep': json.loads((ROOT / 'results/window-sweep-v0.4/sweep.json').read_text())}


def csv_rows(records):
    return [{**r['condition'], **r['metrics'], **{'alert_window_' + k: v for k, v in r['window_flags'].items()}}
            for r in records]


def run(out):
    if out.exists():
        raise ValueError('Use a new output directory; evidence is never overwritten')
    inputs = input_hashes()
    config = json.loads((ROOT / PLAN['config_source']).read_text())
    out.mkdir(parents=True)
    (out / 'plan.json').write_text(json.dumps(PLAN, indent=2) + '\n')
    (out / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    records = []
    draws = json.loads((ROOT / 'results/evasion-family-v0.5-window180/draws.json').read_text())['draws']
    for d in draws:
        m, _ = evasion_episode(config, d['market_seed'], {k: d[k] for k in PARAMS}, 'aggregate', False)
        records.append(annotate(m, config, 'gated_unresponsive_reference', d['market_seed'], 'aggregate', draw=d['draw']))
    for cadence in PLAN['cadence_seconds']:
        for gate in PLAN['gates']:
            for seed in range(6000, 6040):
                m = maker_episode(config, seed, gate, cadence)
                records.append(annotate(m, config, 'patient_maker', seed, gate, cadence=cadence))
    with (out / 'traces.jsonl.gz').open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as f:
        for r in records:
            f.write((canonical(r) + '\n').encode())
    rows = csv_rows(records)
    with (out / 'episodes.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    (out / 'summary.json').write_text(json.dumps(summarize(records), indent=2) + '\n')
    sources = sorted((ROOT / 'pilot').glob('*.py')) + sorted((ROOT / 'paper_study').glob('*.py'))
    manifest = {'python': platform.python_version(), 'input_sha256': inputs,
                'source_sha256': {str(p.relative_to(ROOT)): sha(p) for p in sources},
                'files': {p.name: sha(p) for p in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return {'episodes': len(records), 'output': str(out), 'llm_episodes': 0}


def verify(out):
    manifest = json.loads((out / 'manifest.json').read_text())
    for name, expected in {**manifest['source_sha256'], **manifest['input_sha256']}.items():
        if sha(ROOT / name) != expected:
            raise AssertionError('Source/input mismatch: ' + name)
    for name, expected in manifest['files'].items():
        if sha(out / name) != expected:
            raise AssertionError('Evidence mismatch: ' + name)
    if input_hashes() != manifest['input_sha256']:
        raise AssertionError('Historical manifest mismatch')
    config = json.loads((out / 'config.json').read_text())
    records = []
    with gzip.open(out / 'traces.jsonl.gz', 'rt') as f:
        for line in f:
            r = json.loads(line); c = r['condition']
            cls = AggregateMarket if c['gate'] == 'aggregate' else Market
            m = cls(config, c['seed'], c['cohort'] != 'gated_unresponsive_reference', c['gate'] != 'off')
            for e in r['events']:
                if e['kind'] == 'attempt':
                    m.advance(e['time']); m.act(e['action'])
            m.advance(config['session_seconds'])
            if annotate(m, config, **c) != r:
                raise AssertionError('Action replay mismatch: ' + str(c))
            records.append(r)
    if summarize(records) != json.loads((out / 'summary.json').read_text()):
        raise AssertionError('Summary mismatch')
    with (out / 'episodes.csv').open(newline='') as f:
        saved = list(csv.DictReader(f))
    expected = [{k: '' if v is None else str(v) for k, v in r.items()} for r in csv_rows(records)]
    if saved != expected:
        raise AssertionError('CSV/trace mismatch')
    return {'episodes_replayed': len(records), 'mismatches': 0, 'csv_and_summary_recomputed': True,
            'historical_source_and_evidence_hashes_verified': True, 'llm_episodes': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('run', 'verify'))
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps((run if args.command == 'run' else verify)(args.output), indent=2))
