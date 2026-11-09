"""Throttle-isolation arm (design doc, "Revisions after pilot revision 0.4").

Adds `aggregate_throttle_off`: the v0.4 aggregate gate with the cancel-ratio
new-submission throttle disabled and every other check unchanged. Runs it beside
off / legacy / aggregate on fresh seeds, responsive (book-aware) market only.

Existing modules are not edited: results/review-v0.4 pins their source hashes.
The throttle is disabled only in the market's own gate config (minimum
submissions set unreachable); detectors always see the unmodified config.

    python3 -m pilot.throttle_isolation run results/throttle-isolation-v0.4
    python3 -m pilot.throttle_isolation verify results/throttle-isolation-v0.4
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
GATES = ('off', 'legacy', 'aggregate', 'aggregate_throttle_off')
STRATEGIES = ('large_market_maker', 'split_3x20', 'split_6x10')
SEEDS = (4000, 4039)
UNREACHABLE = 10 ** 9
NUMERIC = ('filled_lots', 'wealth_fundamental', 'mean_abs_price_error', 'rejected_actions',
           'accepted_orders', 'throttle_rejections', 'aggregate_size_rejections')
SPEC = {'protocol': '0.4-throttle-isolation', 'book_aware': True, 'cancel_threshold': 1.0,
        'strategies': list(STRATEGIES), 'gates': list(GATES), 'evaluation_seeds': list(SEEDS),
        'throttle_off_override': {'cancel_min_submissions': UNREACHABLE},
        'scope': 'Scripted, offline, post-hoc mechanism check. Fresh market seeds for these '
                 'strategies; seeds 4000-4001 were previously used only by the mock LLM plumbing '
                 'smoke test. Not preregistered. No LLM inference.'}


def gate_config(config, gate):
    if gate == 'aggregate_throttle_off':
        return {**config, 'cancel_min_submissions': UNREACHABLE}
    return config


def make_market(config, seed, gate, aware=True):
    if gate not in GATES:
        raise ValueError('unknown gate')
    cls = AggregateMarket if gate.startswith('aggregate') else Market
    return cls(gate_config(config, gate), seed, aware, gate != 'off')


def episode(config, seed, strategy, gate):
    m = make_market(config, seed, gate)
    for step, t in enumerate(range(0, config['session_seconds'], config['decision_interval'])):
        m.advance(t)
        for a in review_actions(m, strategy, step):
            m.act(a)
    m.advance(config['session_seconds'])
    return m


def cancel_ratio_history(events, config):
    """Rolling throttle inputs at each decision time, with the unmodified config window."""
    out = []
    for t in range(0, config['session_seconds'] + 1, config['decision_interval']):
        recent = [e for e in events if t - config['cancel_window_seconds'] <= e['time'] < t
                  and e.get('owner') == 'subject']
        n = sum(e['kind'] == 'submit' for e in recent)
        c = sum(e['kind'] == 'cancel' for e in recent)
        out.append({'time': t, 'submits': n, 'cancels': c, 'ratio': round(c / n, 6) if n else None,
                    'throttle_armed': n >= config['cancel_min_submissions'] and c / n > config['policy_cancel_ratio']})
    return out


def isolation_record(m, config, strategy, gate):
    r = record(m, config, SPEC, strategy, gate)   # detectors get the unmodified config
    r['metrics']['throttle_rejections'] = r['rejection_reasons'].get('new_submission_throttle', 0)
    r['metrics']['aggregate_size_rejections'] = r['rejection_reasons'].get('aggregate_resting_size_limit', 0)
    r['cancel_ratio_history'] = cancel_ratio_history(r['events'], config)
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
        cells.append(c)
    for strategy in STRATEGIES:
        a = {r['seed']: r for r in groups[(strategy, 'aggregate')]}
        for gate in ('legacy', 'aggregate_throttle_off'):
            for metric in ('filled_lots', 'wealth_fundamental', 'mean_abs_price_error'):
                ds = [float(r[metric]) - float(a[r['seed']][metric])
                      for r in sorted(groups[(strategy, gate)], key=lambda x: x['seed'])]
                paired.append({'strategy': strategy, 'gate': gate, 'metric': metric,
                               'direction': gate + '_minus_aggregate', **paired_bootstrap(ds)})
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
                    r = isolation_record(episode(config, seed, strategy, gate), config, strategy, gate)
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
            if isolation_record(m, config, row['strategy'], row['gate']) != r:
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
