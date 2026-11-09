"""Run, replay and compare the exploratory review extension with stdlib only."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import platform
import statistics
from collections import Counter, defaultdict
from .run import canonical, digest, subject_events
from .stats import wilson, paired_bootstrap
from .revision import review_episode, make_market, extended_detect
from .detectors import detect

ROOT = Path(__file__).resolve().parents[1]
NUMERIC = ('filled_lots', 'wealth_fundamental', 'mean_abs_price_error', 'rejected_actions', 'accepted_orders')


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r['strategy'], r['book_aware'], r['gate'])].append(r)
    cells, paired = [], []
    for (strategy, aware, gate), rs in sorted(groups.items()):
        c = dict(strategy=strategy, book_aware=aware, gate=gate, n=len(rs))
        for name in ('legacy_conduct', 'extended_conduct', 'aggregate_spoof_pattern', 'inactive'):
            k = sum(r[name] for r in rs)
            c[name] = {'k': k, 'rate': k/len(rs), 'ci95': wilson(k, len(rs))}
        c.update({k: statistics.mean(r[k] for r in rs) for k in NUMERIC})
        cells.append(c)
        if gate != 'off':
            base = {r['seed']: r for r in groups[(strategy, aware, 'off')]}
            for metric in NUMERIC + ('legacy_conduct', 'extended_conduct'):
                ds = [float(r[metric]) - float(base[r['seed']][metric]) for r in sorted(rs,key=lambda x:x['seed'])]
                paired.append(dict(strategy=strategy, book_aware=aware, gate=gate, metric=metric,
                                   direction='gate_minus_off', **paired_bootstrap(ds)))
    return {'episode_count':len(rows), 'llm_episodes':0, 'cells':cells, 'paired_effects':paired}


def record(m, config, spec, strategy, gate):
    ev = subject_events(m.events)
    legacy = detect(ev, config, spec['cancel_threshold'])
    extended = extended_detect(ev, config, spec['cancel_threshold'])
    names = {a['detector'] for a in extended}
    row = dict(strategy=strategy, seed=m.seed, book_aware=m.book_aware, gate=gate, **m.metrics(),
               legacy_conduct=any(a['detector']!='excessive_cancels' for a in legacy),
               extended_conduct=bool(names-{'excessive_cancels'}),
               aggregate_spoof_pattern='aggregate_spoof_pattern' in names)
    return {'metrics':row, 'events':ev, 'legacy_alerts':legacy, 'extended_alerts':extended,
            'rejection_reasons':dict(sorted(Counter(e['reason'] for e in ev if e['kind']=='reject').items())),
            'full_event_sha256':digest(m.events), 'full_event_count':len(m.events)}


def run(out):
    if out.exists(): raise SystemExit('Use a new output directory; existing evidence is never overwritten.')
    out.mkdir(parents=True)
    config = json.loads((ROOT/'config.json').read_text())
    spec = json.loads((ROOT/'review_config.json').read_text())
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    (out/'review_config.json').write_text(json.dumps(spec,indent=2)+'\n')
    rows=[]
    with (out/'traces.jsonl.gz').open('wb') as raw, gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0) as trace:
        for strategy in spec['strategies']:
            for aware in (False, True):
                for gate in spec['gates']:
                    for seed in range(spec['evaluation_seeds'][0],spec['evaluation_seeds'][1]+1):
                        r=record(review_episode(config,seed,strategy,aware,gate),config,spec,strategy,gate)
                        rows.append(r['metrics']); trace.write((canonical(r)+'\n').encode())
            print('Completed '+strategy,flush=True)
    with (out/'episodes.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (out/'summary.json').write_text(json.dumps(summarize(rows),indent=2)+'\n')
    sources=list(sorted((ROOT/'pilot').glob('*.py')))+[ROOT/'review_config.json']
    manifest={'python':platform.python_version(),'scope':spec['scope'],
              'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
              'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir())}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


def verify(out):
    manifest=json.loads((out/'manifest.json').read_text())
    for name,expected in manifest['source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:raise AssertionError('Source mismatch: '+name)
    for name,expected in manifest['files'].items():
        if hashlib.sha256((out/name).read_bytes()).hexdigest()!=expected:raise AssertionError('File mismatch: '+name)
    config=json.loads((out/'config.json').read_text());spec=json.loads((out/'review_config.json').read_text())
    rows=[]
    with gzip.open(out/'traces.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line); row=r['metrics']
            m=make_market(config,row['seed'],row['book_aware'],row['gate'])
            for e in r['events']:
                if e['kind']=='attempt':m.advance(e['time']);m.act(e['action'])
            m.advance(config['session_seconds'])
            if record(m,config,spec,row['strategy'],row['gate'])!=r:raise AssertionError('Replay mismatch')
            rows.append(row)
    with (out/'episodes.csv').open() as f: csvrows=list(csv.DictReader(f))
    if [{k:str(v) for k,v in r.items()} for r in rows]!=csvrows:raise AssertionError('CSV mismatch')
    if summarize(rows)!=json.loads((out/'summary.json').read_text()):raise AssertionError('Summary mismatch')
    return {'episodes_replayed':len(rows),'event_alert_metric_mismatches':0,'csv_verified':True,'summary_recomputed':True,'source_and_evidence_hashes_verified':True}


def compare(a,b):
    names=('config.json','review_config.json','episodes.csv','summary.json','traces.jsonl.gz')
    checks={n:hashlib.sha256((a/n).read_bytes()).digest()==hashlib.sha256((b/n).read_bytes()).digest() for n in names}
    if not all(checks.values()):raise AssertionError(checks)
    return {'equal':True,'files':checks}


if __name__=='__main__':
    p=argparse.ArgumentParser();sp=p.add_subparsers(dest='command',required=True)
    for name in ('run','verify'):
        sub=sp.add_parser(name);sub.add_argument('output',type=Path)
    sub=sp.add_parser('compare');sub.add_argument('reference',type=Path);sub.add_argument('reproduced',type=Path)
    a=p.parse_args()
    if a.command=='run':run(a.output)
    elif a.command=='verify':print(json.dumps(verify(a.output),indent=2))
    else:print(json.dumps(compare(a.reference,a.reproduced),indent=2))
