"""Check the revised manuscript's added claims against saved episode evidence."""
import gzip
import hashlib
import json
from pathlib import Path

from pilot.revision import extended_detect

ROOT = Path(__file__).resolve().parents[2]


def main():
    report = json.loads((ROOT/'results/reporting-v2_3.json').read_text())
    boundary = json.loads((ROOT/'results/boundary-v1/summary.json').read_text())
    checks = {}
    assert boundary['episode_count'] == 560
    assert boundary['llm_episodes'] == 0
    assert boundary['cadence_cells'] == [
        {'cadence': 170, 'n': 40, 'conduct_alerts': 39},
        {'cadence': 190, 'n': 40, 'conduct_alerts': 0}]
    checks['cadence'] = boundary['cadence_cells']
    checks['price_boundaries'] = []
    for cap, threshold, expected in [(2,.24,48),(2,.26,0),(4,.39,48),(4,.41,0),(6,.49,48),(6,.51,0)]:
        pairs = [p for p in boundary['paired_differences'] if p['strategy']=='split_6x10'
                 and p['gate']=='aggregate' and p['cap']==cap and p['threshold']==threshold]
        assert len(pairs) == 10
        assert all(p['response_wealth_difference'] == expected for p in pairs)
        checks['price_boundaries'].append({'cap':cap,'threshold':threshold,'n':10,
                                           'min_delta':expected,'max_delta':expected})
    def cell(source, **kw):
        return next(c for c in report['cells'] if c['source']==source and all(c[k]==v for k,v in kw.items()))
    checks['maker_costs'] = {}
    for strategy in ('large_market_maker','patient_large_maker'):
        off = cell('negative-controls-v0.4',strategy=strategy,gate='off')
        gate = cell('negative-controls-v0.4',strategy=strategy,gate='aggregate')
        cost = 100*(off['filled_lots']['mean']-gate['filled_lots']['mean'])/off['filled_lots']['mean']
        checks['maker_costs'][strategy] = {'percent_volume_loss':cost,
                                          'rejections':{k:v['mean'] for k,v in gate['rejections'].items()}}
    large = checks['maker_costs']['large_market_maker']['rejections']
    assert abs(sum(large[k] for k in ('cap','throttle','position','other')) - 27.95) < 1e-9
    assert checks['maker_costs']['patient_large_maker']['rejections']['throttle'] == 0
    checks['parameter_ranges'] = [c for c in report['paired_cells'] if c['cohort']=='parameter_replication']
    for c in checks['parameter_ranges']:
        assert c['delta']['n'] == 60
    checks['horizon_baselines'] = {str(h):cell('sensitivity-v1',cohort='throttle_horizon',horizon=h,gate='off')['filled_lots'] for h in (720,3600)}
    assert checks['horizon_baselines']['720']['mean'] == 60.5
    assert checks['horizon_baselines']['3600']['mean'] == 300.6
    for c in report['paired_cells']:
        if c['cohort']=='cap_response' and c['strategy']=='split_6x10':
            assert c['delta']['min'] == c['delta']['max']
    n = 0
    cfg = json.loads((ROOT/'results/review-v0.4/config.json').read_text())
    with gzip.open(ROOT/'results/review-v0.4/traces.jsonl.gz','rt') as stream:
        for line in stream:
            r = json.loads(line); m = r['metrics']
            if m['strategy']=='split_6x10' and m['book_aware']:
                # Compare each signal, not just the union, when changing W.
                flags = [{a['detector'] for a in extended_detect(r['events'],dict(cfg,spoof_window_seconds=w))
                          if a['detector']!='excessive_cancels'} for w in (120,180)]
                assert flags[0] == flags[1]
                n += 1
    assert n == 120
    checks['table2_rescored_episodes'] = n
    verification = json.loads((ROOT/'results/boundary-v1/verification.json').read_text())
    assert verification['episodes_replayed']==560 and verification['record_mismatches']==0
    checks['replay'] = verification
    checks.update({'document_version':'2.3','date':'2026-11-10','all_assertions_passed':True,
                   'scope':'Internal consistency audit; not independent scientific validation',
                   'report_sha256':hashlib.sha256((ROOT/'results/reporting-v2_3.json').read_bytes()).hexdigest(),
                   'historical_evidence_modified':False})
    (ROOT/'paper/offline_study/revision_v2_3_check.json').write_text(json.dumps(checks,indent=2)+'\n')
    print(json.dumps({'checks_passed':True,'new_episodes':560,'table2_rescored':n}))


if __name__ == '__main__':
    main()
