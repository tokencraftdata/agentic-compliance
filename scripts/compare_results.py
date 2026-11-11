"""Compare numeric evidence and deterministic traces from two complete batches."""
import argparse
import hashlib
import json
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('reference',type=Path); p.add_argument('reproduced',type=Path)
a=p.parse_args()
files=['config.json','calibration.json','episodes.csv','summary.json','traces.jsonl.gz']
checks={name: hashlib.sha256((a.reference/name).read_bytes()).hexdigest()==hashlib.sha256((a.reproduced/name).read_bytes()).hexdigest() for name in files}
print(json.dumps({'equal':all(checks.values()),'files':checks},indent=2))
if not all(checks.values()): raise SystemExit(1)
