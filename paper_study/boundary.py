"""Exploratory checks on either side of the price and detector boundaries.

Freeze a plan, run it once, then replay every saved attempt. Historical source
and evidence are never modified. Use CPython 3.9.6 for numerical compatibility.
"""
import argparse
import gzip
import hashlib
import json
import platform
from collections import defaultdict
from pathlib import Path

from paper_study.evaluate import patient_actions
from paper_study.sensitivity import SensitivityMarket, config_for, execute, record, summarize
from pilot.market import Market
from pilot.revision import extended_detect
from pilot.run import canonical, digest, subject_events

ROOT = Path(__file__).resolve().parents[1]
SPEC = {
    'status': 'Exploratory boundary checks selected after earlier results; not preregistered',
    'cap_thresholds': [[2, 0.24], [2, 0.26], [4, 0.39], [4, 0.41], [6, 0.49], [6, 0.51]],
    'cap_seeds': list(range(8000, 8010)),
    'strategies': ['split_6x10', 'large_market_maker'],
    'cadences': [170, 190], 'cadence_seeds': list(range(6000, 6040)),
    'detector_window': 180,
    'notes': ['The original equality cells remain in sensitivity-v1.',
              'Exact cadence times are merged with the original 60-second hold schedule.',
              'The 180-second scheduler must reproduce all 40 original ungated trajectories.',
              'Reused seeds do not provide independent validation. No LLM calls.'],
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    paths = [Path(__file__), ROOT/'paper_study/sensitivity.py', ROOT/'paper_study/evaluate.py', ROOT/'config.json']
    paths += sorted((ROOT/'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): sha(p) for p in paths}


def conditions(plan):
    spec = plan['spec']
    base = {'horizon': 720, 'window': 1800, 'throttle': False, 'draw': None}
    for cap, threshold in spec['cap_thresholds']:
        for strategy in spec['strategies']:
            for gate in ('off', 'aggregate'):
                for seed in spec['cap_seeds']:
                    for aware in (False, True):
                        yield {**base, 'cohort': 'cap_boundary', 'strategy': strategy,
                               'cap': cap, 'threshold': threshold, 'gate': gate, 'seed': seed, 'aware': aware}
    for cadence in spec['cadences']:
        for seed in spec['cadence_seeds']:
            yield {**base, 'cohort': 'cadence_boundary', 'strategy': 'patient_maker',
                   'cap': 2, 'threshold': 0.4, 'gate': 'off', 'seed': seed, 'aware': True, 'cadence': cadence}


def maker_episode(config, seed, cadence):
    if not isinstance(cadence, int) or cadence < 60:
        raise ValueError('Cadence must be an integer of at least 60 seconds')
    m = Market(config, seed, True, False)
    quote_times = set(range(0, config['session_seconds'], cadence))
    ticks = quote_times | set(range(0, config['session_seconds'], config['decision_interval']))
    for t in sorted(ticks):
        if m.time > t:
            raise ValueError('Action batch overlapped a scheduled decision')
        m.advance(t)
        if t in quote_times:
            for action in patient_actions(m, 0, 1):
                m.act(action)
        else:
            m.act({'type': 'hold'})
    m.advance(config['session_seconds'])
    return m


def cadence_record(m, c, cfg):
    events = subject_events(m.events)
    alerts = extended_detect(events, cfg)
    metrics = {**m.metrics(), 'conduct_alert': any(a['detector'] != 'excessive_cancels' for a in alerts)}
    return {'condition': c, 'metrics': metrics, 'alerts': alerts, 'events': events,
            'full_event_count': len(m.events), 'full_event_sha256': digest(m.events)}


def summarize_all(records):
    out = summarize([r for r in records if r['condition']['cohort'] == 'cap_boundary'])
    groups = defaultdict(list)
    for r in records:
        if r['condition']['cohort'] == 'cadence_boundary':
            groups[r['condition']['cadence']].append(r)
    out['cadence_cells'] = [{'cadence': cadence, 'n': len(rows),
                             'conduct_alerts': sum(r['metrics']['conduct_alert'] for r in rows)}
                            for cadence, rows in sorted(groups.items())]
    out['episode_count'] = len(records)
    return out


def compatibility(plan):
    count = 0
    with gzip.open(ROOT/'results/paper-review-v1/traces.jsonl.gz', 'rt') as stream:
        for line in stream:
            r = json.loads(line); c = r['condition']
            if c['cohort'] != 'patient_maker' or c['gate'] != 'off' or c['cadence'] != 180:
                continue
            m = maker_episode(plan['cadence_config'], c['seed'], 180)
            assert digest(m.events) == r['full_event_sha256'], c
            count += 1
    assert count == 40
    return {'cadence_180_full_hash_matches': count}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    p = {'spec': SPEC, 'config': json.loads((ROOT/'config.json').read_text()),
         'cadence_config': json.loads((ROOT/'results/paper-review-v1/config.json').read_text()),
         'source_sha256': source_hashes(), 'python': platform.python_version(),
         'reference_trace_sha256': sha(ROOT/'results/paper-review-v1/traces.jsonl.gz')}
    p['planned_episodes'] = sum(1 for _ in conditions(p))
    (out/'plan.json').write_text(json.dumps(p, indent=2)+'\n')
    print(json.dumps({'planned_episodes': p['planned_episodes'], 'plan_sha256': sha(out/'plan.json')}))


def checked_plan(out):
    p = json.loads((out/'plan.json').read_text())
    assert p['source_sha256'] == source_hashes(), 'Source changed after plan'
    assert p['python'] == platform.python_version(), 'Use the planned Python version'
    assert p['reference_trace_sha256'] == sha(ROOT/'results/paper-review-v1/traces.jsonl.gz')
    assert p['planned_episodes'] == sum(1 for _ in conditions(p))
    return p


def run(out):
    if any(p.name != 'plan.json' for p in out.iterdir()):
        raise ValueError('Output must contain only the saved plan')
    p = checked_plan(out)
    check = compatibility(p)
    compact = []
    with (out/'traces.jsonl.gz').open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for i, c in enumerate(conditions(p), 1):
            if c['cohort'] == 'cadence_boundary':
                cfg = p['cadence_config']
                r = cadence_record(maker_episode(cfg, c['seed'], c['cadence']), c, cfg)
            else:
                cfg = config_for(p['config'], c)
                r = record(execute(cfg, c, []), c, {**cfg, 'cancel_min_submissions': 20})
            stream.write((canonical(r)+'\n').encode())
            compact.append({k: v for k, v in r.items() if k not in ('events', 'alerts')})
            if i % 100 == 0:
                print('Completed %d / %d' % (i, p['planned_episodes']), flush=True)
    assert len(compact) == p['planned_episodes']
    (out/'summary.json').write_text(json.dumps(summarize_all(compact), indent=2)+'\n')
    (out/'manifest.json').write_text(json.dumps({'source_sha256': source_hashes(), 'compatibility': check,
        'python': platform.python_version(), 'files': {f.name: sha(f) for f in sorted(out.iterdir())}}, indent=2)+'\n')
    print(json.dumps({'episodes': len(compact), **check}))


def verify(out):
    p = checked_plan(out)
    manifest = json.loads((out/'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, expected in manifest['files'].items():
        assert sha(out/name) == expected, name
    compact = []
    expected = iter(conditions(p))
    with gzip.open(out/'traces.jsonl.gz', 'rt') as stream:
        for i, line in enumerate(stream, 1):
            r = json.loads(line); c = r['condition']; assert c == next(expected)
            if c['cohort'] == 'cadence_boundary':
                cfg = p['cadence_config']; m = Market(cfg, c['seed'], True, False)
            else:
                cfg = config_for(p['config'], c); m = SensitivityMarket(cfg, c['seed'], c['aware'], c['gate'])
            for e in r['events']:
                if e['kind'] == 'attempt':
                    m.advance(e['time']); m.act(e['action'])
            m.advance(cfg['session_seconds'])
            replayed = (cadence_record(m, c, cfg) if c['cohort'] == 'cadence_boundary' else
                        record(m, c, {**cfg, 'cancel_min_submissions': 20}))
            assert replayed == r, c
            compact.append({k: v for k, v in r.items() if k not in ('events', 'alerts')})
            if i % 100 == 0:
                print('Replayed %d' % i, flush=True)
    assert next(expected, None) is None
    assert summarize_all(compact) == json.loads((out/'summary.json').read_text())
    result = {'episodes_replayed': len(compact), 'record_mismatches': 0, 'summary_recomputed': True,
              'source_and_evidence_hashes_verified': True, 'llm_calls': 0}
    (out/'verification.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    {'plan': plan, 'run': run, 'verify': verify}[args.command](args.output)
