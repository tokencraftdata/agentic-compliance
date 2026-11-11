"""Post-hoc one-at-a-time diagnostic; no threshold selection or confirmation."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from pilot.detectors import detect
from pilot.revision import extended_detect

GRID={
 'spoof_size_multiple':[2,3,4,6,7],
 'spoof_window_seconds':[1,2,3,5,30,120,300],
 'spoof_max_distance_ticks':[0,1,2,3,5],
 'spoof_max_filled_fraction':[0,0.05,0.1,0.25],
 'layer_min_levels':[2,3,4],
 'cancel_threshold':[0.5,0.75,0.9,1.0],
}


def sweep(source):
    config=json.loads((source/'config.json').read_text())
    with gzip.open(source/'traces.jsonl.gz','rt') as f:
        records=[r for r in map(json.loads,f) if r['metrics']['book_aware'] and r['metrics']['gate']=='off']
    rows=[]
    for parameter,values in GRID.items():
        for value in values:
            cfg=dict(config);threshold=1.0
            if parameter=='cancel_threshold':threshold=value
            else:cfg[parameter]=value
            groups=defaultdict(list)
            for r in records:
                old=detect(r['events'],cfg,threshold);new=extended_detect(r['events'],cfg,threshold)
                groups[r['metrics']['strategy']].append((old,new))
            for strategy,rs in sorted(groups.items()):
                rows.append(dict(parameter=parameter,value=value,strategy=strategy,n=len(rs),
                    legacy_conduct=sum(any(a['detector']!='excessive_cancels' for a in old) for old,new in rs),
                    extended_conduct=sum(any(a['detector']!='excessive_cancels' for a in new) for old,new in rs),
                    excessive_cancels=sum(any(a['detector']=='excessive_cancels' for a in new) for old,new in rs)))
    return {'scope':'Exploratory one-at-a-time sweeps of stored unguarded book-aware traces; all other parameters fixed. Grid extremes are not exhaustive breakpoint estimates. Not an independent detector validation.',
            'traces_sha256':hashlib.sha256((source/'traces.jsonl.gz').read_bytes()).hexdigest(),'grid':GRID,'rows':rows}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    if a.output.exists():raise SystemExit('Output already exists; use a new filename.')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(sweep(a.source),indent=2)+'\n')
