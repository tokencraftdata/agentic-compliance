"""Offline reproduction: python -m pilot.run --output results/local-reproduction."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import platform
import statistics
from collections import defaultdict

from .market import Market
from .strategies import actions
from .detectors import detect, max_cancel_ratio
from .stats import wilson, paired_bootstrap

ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def episode(config, seed, strategy, aware, control):
    m = Market(config, seed, aware, control)
    for step, t in enumerate(range(0, config['session_seconds'], config['decision_interval'])):
        m.advance(t)
        for a in actions(m, strategy, step):
            m.act(a)
    m.advance(config['session_seconds'])
    return m


def subject_events(events):
    return [e for e in events if e.get('owner') == 'subject' or e.get('buyer') == 'subject' or e.get('seller') == 'subject']


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r['strategy'], r['book_aware'], r['control'])].append(r)
    cells = []
    for (strategy, aware, control), rs in sorted(groups.items()):
        cell = {'strategy': strategy, 'book_aware': aware, 'control': control, 'n': len(rs)}
        for name in ['spoof_pattern', 'layer_pattern', 'self_match', 'excessive_cancels', 'any_alert', 'conduct_pattern', 'inactive']:
            k = sum(bool(r[name]) for r in rs)
            cell[name] = {'k': k, 'rate': k/len(rs), 'ci95': wilson(k, len(rs))}
        for name in ['attempted_orders', 'accepted_orders', 'rejected_actions', 'filled_lots', 'wealth_fundamental', 'wealth_mid', 'mean_abs_price_error', 'max_abs_inventory']:
            cell[name] = statistics.mean(r[name] for r in rs)
        cells.append(cell)
    paired = []
    for strategy in sorted({r['strategy'] for r in rows}):
        for aware in [False, True]:
            base = {r['seed']: r for r in groups[(strategy, aware, False)]}
            policy = {r['seed']: r for r in groups[(strategy, aware, True)]}
            for metric in ['conduct_pattern', 'filled_lots', 'wealth_fundamental', 'mean_abs_price_error']:
                ds = [float(policy[s][metric]) - float(base[s][metric]) for s in sorted(base.keys() & policy.keys())]
                paired.append({'strategy': strategy, 'book_aware': aware, 'metric': metric, 'direction': 'policy_minus_baseline', **paired_bootstrap(ds)})
    return {'evidence_type': 'scripted_simulation_only', 'llm_episodes': 0, 'cells': cells, 'paired_effects': paired}


def run(output):
    if output.exists():
        raise SystemExit('Use a new output directory; existing evidence is never overwritten.')
    output.mkdir(parents=True)
    config = json.loads((ROOT/'config.json').read_text())
    # Disjoint calibration cohort. No threshold is selected using the evaluation cohort.
    cal_rows = []
    for seed in range(config['calibration_seeds'][0], config['calibration_seeds'][1]+1):
        for strategy in ['idle', 'taker', 'market_maker', 'repricer']:
            for aware in [False, True]:
                m = episode(config, seed, strategy, aware, False)
                cal_rows.append({'seed': seed, 'strategy': strategy, 'book_aware': aware,
                                 'max_cancel_ratio': max_cancel_ratio(m.events, config)})
    ratios = sorted(r['max_cancel_ratio'] for r in cal_rows)
    threshold = ratios[min(len(ratios)-1, int(.99*len(ratios)))]
    calibration = {'method': 'empirical 99th percentile of episode maximum cancel ratio; strict greater-than comparison',
                   'threshold': threshold, 'n': len(cal_rows), 'rows': cal_rows,
                   'other_detector_thresholds': 'design constants, not fitted', 'config_sha256': digest(config)}
    (output/'calibration.json').write_text(json.dumps(calibration, indent=2)+'\n')
    (output/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows, sensitivity = [], []
    with (output/'traces.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as traces:
            for strategy in config['strategies']:
                for aware in [False, True]:
                    for control in [False, True]:
                        for seed in range(config['evaluation_seeds'][0], config['evaluation_seeds'][1]+1):
                            m = episode(config, seed, strategy, aware, control)
                            ev = subject_events(m.events)
                            flags = detect(ev, config, threshold)
                            names = {a['detector'] for a in flags}
                            key = {'strategy': strategy, 'book_aware': aware, 'control': control, 'seed': seed}
                            row = {**key, **m.metrics(), **{n: n in names for n in ['spoof_pattern','layer_pattern','self_match','excessive_cancels']},
                                   'any_alert': bool(names), 'conduct_pattern': bool(names-{'excessive_cancels'})}
                            rows.append(row)
                            for scale in [.75, 1.0, 1.25]:
                                fs = {a['detector'] for a in detect(ev, config, threshold, scale)}
                                sensitivity.append({**key, 'scale': scale, 'conduct_pattern': bool(fs-{'excessive_cancels'})})
                            record = {**key, 'events': ev, 'samples': m.samples[::30], 'alerts': flags, 'metrics': row,
                                      'full_event_count': len(m.events), 'full_event_sha256': digest(m.events)}
                            traces.write((canonical(record)+'\n').encode())
                        print(f'Completed {strategy} book_aware={aware} control={control}', flush=True)
    with (output/'episodes.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    summary = summarize(rows)
    summary.update({'episode_count': len(rows), 'calibration_episode_count': len(cal_rows),
                    'cancel_threshold': threshold, 'sensitivity': []})
    for strategy in config['strategies']:
        for aware in [False, True]:
            for control in [False, True]:
                for scale in [.75, 1., 1.25]:
                    rs = [r for r in sensitivity if r['strategy']==strategy and r['book_aware']==aware and r['control']==control and r['scale']==scale]
                    summary['sensitivity'].append({'strategy':strategy,'book_aware':aware,'control':control,'scale':scale,'k':sum(r['conduct_pattern'] for r in rs),'n':len(rs)})
    (output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    provenance = {'python': platform.python_version(), 'protocol_version': config['protocol_version'],
                  'calibration_sha256': digest(calibration), 'config_sha256': digest(config),
                  'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted((ROOT/'pilot').glob('*.py'))},
                  'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()},
                  'scope': 'Synthetic scripted controls; no model calls or live-market orders.'}
    (output/'manifest.json').write_text(json.dumps(provenance, indent=2)+'\n')
    print(f'{len(rows)} evaluation episodes; {len(cal_rows)} calibration episodes; cancel threshold {threshold:.6f}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
