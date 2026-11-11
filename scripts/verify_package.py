"""Check manifest integrity and consistency between traces, CSV and summary."""
import csv
import gzip
import hashlib
import json
from pathlib import Path
from pilot.run import summarize, digest
root=Path(__file__).resolve().parents[1]
result=root/'results/offline-v2'
manifest=json.loads((result/'manifest.json').read_text())
for name,expected in manifest['source_sha256'].items():
 if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected: raise AssertionError('Source hash mismatch: '+name)
for name,expected in manifest['files'].items():
 if hashlib.sha256((result/name).read_bytes()).hexdigest()!=expected: raise AssertionError('Evidence hash mismatch: '+name)
with gzip.open(result/'traces.jsonl.gz','rt') as f: rows=[json.loads(line)['metrics'] for line in f]
with (result/'episodes.csv').open() as f: csvrows=list(csv.DictReader(f))
if len(rows)!=len(csvrows): raise AssertionError('Row count mismatch')
for r,c in zip(rows,csvrows):
 if {k:str(v) for k,v in r.items()}!=c: raise AssertionError('CSV differs from trace metrics')
saved=json.loads((result/'summary.json').read_text())
actual=summarize(rows)
for key in actual:
 if actual[key]!=saved[key]: raise AssertionError('Summary differs: '+key)
if digest(json.loads((result/'config.json').read_text()))!=manifest['config_sha256']:raise AssertionError('Config mismatch')
print(json.dumps({'source_hashes_verified':len(manifest['source_sha256']),'evidence_hashes_verified':len(manifest['files']),
 'csv_trace_rows_equal':len(rows),'summary_recomputed':True},indent=2))
