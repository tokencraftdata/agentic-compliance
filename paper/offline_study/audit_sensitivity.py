"""Independently aggregate the revised paper's central numbers from saved records."""
import gzip
import hashlib
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'results/sensitivity-v1'


def audit():
    rows=[]
    with gzip.open(FOLDER/'traces.jsonl.gz','rt') as stream:
        for line in stream:
            r=json.loads(line)
            rows.append({'c':r['condition'],'m':r['metrics']})
    assert len(rows)==2140
    def selected(**filters):
        return [r for r in rows if all(r['c'][k]==v for k,v in filters.items())]
    def paired(**filters):
        rs=selected(**filters)
        out={}
        for r in rs:
            key=(r['c']['seed'],r['c']['draw'])
            out.setdefault(key,{})[r['c']['aware']]=r['m']
        assert all(set(v)=={False,True} for v in out.values())
        return [{'delta':round(v[True]['wealth_fundamental']-v[False]['wealth_fundamental'],6),
                 'alert':v[True]['conduct_alert']} for v in out.values()]
    grid={}
    for threshold in (0.2,0.4):
        pairs=paired(cohort='cap_response',strategy='split_6x10',gate='aggregate',cap=2,threshold=threshold)
        assert len(pairs)==10
        grid[str(threshold)]={'pairs':len(pairs),'mean_delta':statistics.mean(r['delta'] for r in pairs),
                              'positive_without_alert':sum(r['delta']>0 and not r['alert'] for r in pairs)}
    assert grid['0.2']['mean_delta']==48 and grid['0.4']['mean_delta']==0
    parameters={}
    for threshold in (0.2,0.4):
        pairs=paired(cohort='parameter_replication',gate='aggregate',threshold=threshold)
        assert len(pairs)==60
        parameters[str(threshold)]={'pairs':len(pairs),'mean_delta':statistics.mean(r['delta'] for r in pairs),
                                    'positive_without_alert':sum(r['delta']>0 and not r['alert'] for r in pairs)}
    assert parameters['0.2']['positive_without_alert']==30
    assert parameters['0.4']['positive_without_alert']==0
    off=selected(cohort='throttle_horizon',horizon=3600,gate='off')
    gated=selected(cohort='throttle_horizon',horizon=3600,gate='aggregate',window=1800,throttle=True)
    a=statistics.mean(r['m']['filled_lots'] for r in off);b=statistics.mean(r['m']['filled_lots'] for r in gated)
    assert a==300.6 and b==74.4 and len(off)==len(gated)==10
    costs={'ungated_lots':a,'aggregate_lots':b,'percent_volume_loss':100*(a-b)/a,
           'episodes_recovered':sum(r['m']['first_recovery'] is not None for r in gated)}
    assert costs['episodes_recovered']==10
    result={'date':'2026-11-10','scope':'Post-hoc numerical consistency check, not independent scientific validation',
            'trace_sha256':hashlib.sha256((FOLDER/'traces.jsonl.gz').read_bytes()).hexdigest(),
            'episodes_read':len(rows),'cap_grid':grid,'parameter_replication':parameters,'one_hour_cost':costs,
            'all_assertions_passed':True}
    (ROOT/'paper/offline_study/research_review_v2.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    audit()
