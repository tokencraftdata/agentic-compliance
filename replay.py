"""Verify saved evidence by replaying attempted actions, not strategy code."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from pilot.market import Market
from pilot.detectors import detect
from pilot.run import digest, subject_events


def verify(root, limit=None):
    config=json.loads((root/'config.json').read_text())
    calibration=json.loads((root/'calibration.json').read_text())
    manifest=json.loads((root/'manifest.json').read_text())
    for name,expected in manifest['files'].items():
        actual=hashlib.sha256((root/name).read_bytes()).hexdigest()
        if actual!=expected: raise AssertionError(f'Artifact hash mismatch: {name}')
    count=0
    with gzip.open(root/'traces.jsonl.gz','rt') as f:
        for line in f:
            r=json.loads(line)
            m=Market(config,r['seed'],r['book_aware'],r['control'])
            for e in r['events']:
                if e['kind']=='attempt':
                    m.advance(e['time']); m.act(e['action'])
            m.advance(config['session_seconds'])
            if digest(m.events)!=r['full_event_sha256']: raise AssertionError('Event replay diverged')
            if len(m.events)!=r['full_event_count']: raise AssertionError('Event count differs')
            if subject_events(m.events)!=r['events']: raise AssertionError('Subject events differ')
            if detect(subject_events(m.events),config,calibration['threshold'])!=r['alerts']: raise AssertionError('Alerts differ')
            for key,value in m.metrics().items():
                if value!=r['metrics'][key]: raise AssertionError(f'Metric differs: {key}')
            count+=1
            if limit and count>=limit: break
    return {'episodes_replayed':count,'event_hash_mismatches':0,'alert_mismatches':0,'metric_mismatches':0}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('results',type=Path); p.add_argument('--limit',type=int)
    args=p.parse_args(); print(json.dumps(verify(args.results,args.limit),indent=2))
