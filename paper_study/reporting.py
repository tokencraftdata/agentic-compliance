"""Derive descriptive spread and rejection categories from saved episode records."""
import argparse
import gzip
import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ['review-v0.4', 'negative-controls-v0.4', 'throttle-isolation-v0.4',
           'paper-review-v1', 'sensitivity-v1', 'boundary-v1']


def describe(values):
    values = list(values)
    if not values:
        raise ValueError('Cannot describe an empty cell')
    return {'n': len(values), 'mean': statistics.mean(values), 'min': min(values), 'max': max(values),
            'sd': statistics.stdev(values) if len(values) > 1 else None}


def rejection_categories(reasons):
    counts = {'cap': 0, 'throttle': 0, 'position': 0, 'other': 0}
    groups = {'resting_size_limit': 'cap', 'aggregate_resting_size_limit': 'cap',
              'new_submission_throttle': 'throttle', 'position_commitment_limit': 'position'}
    for reason, count in reasons.items():
        counts[groups.get(reason, 'other')] += count
    counts['total'] = sum(reasons.values())
    assert sum(v for k, v in counts.items() if k != 'total') == counts['total']
    return counts


def generate():
    groups, paired, inputs = defaultdict(list), defaultdict(dict), {}
    for folder in FOLDERS:
        path = ROOT/'results'/folder/'traces.jsonl.gz'
        inputs[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        with gzip.open(path, 'rt') as stream:
            for line in stream:
                r = json.loads(line)
                c = r.get('condition') or {k: r['metrics'][k] for k in ('strategy', 'gate', 'book_aware', 'seed')}
                key = json.dumps({'source': folder, **{k: v for k, v in c.items() if k not in ('seed', 'draw')}}, sort_keys=True)
                # The saved reason is the first failed check, not all violated rules.
                reasons = r.get('rejection_reasons')
                if reasons is None:
                    reasons = dict(Counter(e['reason'] for e in r['events'] if e['kind'] == 'reject'))
                assert sum(reasons.values()) == r['metrics']['rejected_actions']
                groups[key].append((r['metrics'], rejection_categories(reasons)))
                if c.get('cohort') in ('cap_response', 'parameter_replication', 'cap_boundary'):
                    pk = json.dumps({'source': folder, **{k: v for k, v in c.items() if k != 'aware'}}, sort_keys=True)
                    assert c['aware'] not in paired[pk], 'Duplicate response state'
                    paired[pk][c['aware']] = r['metrics']['wealth_fundamental']
    cells = []
    for key, rows in sorted(groups.items()):
        cells.append({**json.loads(key), 'n': len(rows),
                      'filled_lots': describe(m['filled_lots'] for m, _ in rows),
                      'wealth_fundamental': describe(m['wealth_fundamental'] for m, _ in rows),
                      'rejections': {k: describe(r[k] for _, r in rows)
                                     for k in ('cap', 'throttle', 'position', 'other', 'total')}})
    deltas = defaultdict(list)
    for key, pair in paired.items():
        assert set(pair) == {False, True}, 'Unmatched market path'
        c = json.loads(key)
        ck = json.dumps({k: v for k, v in c.items() if k not in ('seed', 'draw')}, sort_keys=True)
        deltas[ck].append(round(pair[True] - pair[False], 6))
    return {'scope': 'Descriptive spread across recorded episodes, not population confidence intervals',
            'rejections': 'First failed validation reason; counts are not causal attributions of lost volume',
            'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'input_trace_sha256': inputs, 'cells': cells,
            'paired_cells': [{**json.loads(k), 'delta': describe(v)} for k, v in sorted(deltas.items())]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    report = generate()
    if args.verify:
        assert report == json.loads(args.output.read_text())
    else:
        with args.output.open('x') as f:
            f.write(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'cells': len(report['cells']), 'paired_cells': len(report['paired_cells']), 'verified': args.verify}))
