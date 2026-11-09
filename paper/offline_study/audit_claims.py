"""Audit five manuscript claims directly from saved episode records.

Run with CPython 3.9.6 from any directory. No trading strategy is executed;
this checks stored-record aggregation and pairing, not external validity.
It refreshes research_review.json beside this script.
"""
from pathlib import Path
import gzip,json,statistics,hashlib
r=Path(__file__).resolve().parents[2];data=r/'results'
def records(name):
 with gzip.open(data/name/'traces.jsonl.gz','rt') as f:
  for line in f:
   x=json.loads(line)
   yield {k:x[k] for k in ('metrics','condition','full_event_sha256','window_flags') if k in x}
new=list(records('paper-review-v1'))
refs={x['condition']['seed']:x for x in new if x['condition']['cohort']=='gated_unresponsive_reference'}
matched=[]
for x in records('evasion-family-v0.5-window180'):
 m=x['metrics']
 if m['gate']=='aggregate' and m['book_aware']:
  q=refs[m['seed']]
  matched.append({'seed':m['seed'],'wealth_difference':m['wealth_fundamental']-q['metrics']['wealth_fundamental'],'event_hash_equal':x['full_event_sha256']==q['full_event_sha256']})
assert len(matched)==40 and all(x['wealth_difference']==0 and x['event_hash_equal'] for x in matched)
cs=[]
for cadence in (120,180,240,300):
 for gate in ('off','aggregate'):
  xs=[x for x in new if x['condition']['cohort']=='patient_maker' and x['condition']['cadence']==cadence and x['condition']['gate']==gate]
  assert len(xs)==40
  cs.append({'cadence':cadence,'gate':gate,'n':40,'alerts_at_window_180':sum(bool(x['window_flags']['180']) for x in xs),'mean_filled_lots':statistics.mean(x['metrics']['filled_lots'] for x in xs)})
assert [x['alerts_at_window_180'] for x in cs if x['gate']=='off']==[40,34,0,0]
repair=[]
for x in records('review-v0.4'):
 m=x['metrics']
 if m['strategy']=='split_6x10':repair.append(m)
legacy=[m for m in repair if m['gate']=='legacy' and m['book_aware']]
assert len(legacy)==40 and all(not m['legacy_conduct'] and m['rejected_actions']==0 for m in legacy)
agg={m['seed']:m for m in repair if m['gate']=='aggregate' and m['book_aware']}
aggref={m['seed']:m for m in repair if m['gate']=='aggregate' and not m['book_aware']}
assert len(agg)==40 and len(aggref)==40 and all(agg[k]['wealth_fundamental']==aggref[k]['wealth_fundamental'] for k in agg)
ths={}
for x in records('throttle-isolation-v0.4'):
 m=x['metrics']
 if m['strategy']=='large_market_maker':ths.setdefault(m['gate'],{})[m['seed']]=m['filled_lots']
assert all(len(x)==40 for x in ths.values())
diffs=[ths['aggregate_throttle_off'][k]-ths['aggregate'][k] for k in sorted(ths['aggregate'])]
assert statistics.mean(diffs)==8.125
claims=[
 {'claim':'Six-by-ten probe bypasses legacy gate and original conduct detectors in all 40 responsive seeds.','checked':True,'evidence':'results/review-v0.4/traces.jsonl.gz','n':len(legacy),'episodes_without_rejection_or_original_alert':sum(m['rejected_actions']==0 and not m['legacy_conduct'] for m in legacy)},
 {'claim':'Aggregate gate removes response-dependent wealth advantage for the six-by-ten probe in the repair cohort.','checked':True,'n_matched_seeds':40,'all_wealth_differences_zero':True},
 {'claim':'40 parameterized gated comparisons have equal wealth and equal full-event hashes.','checked':True,'n':40,'all_wealth_differences_zero':True,'all_event_hashes_equal':True,'paired_records':matched},
 {'claim':'Legitimate-maker alerts depend on quote cadence, with 34/40 at 180 seconds and 0/40 at 300 seconds.','checked':True,'cadence_cells':cs},
 {'claim':'Disabling only the aggregate submission throttle restores 8.13 lots after rounding.','checked':True,'paired_n':40,'exact_mean_lot_difference':statistics.mean(diffs),'means':{g:statistics.mean(xs.values()) for g,xs in ths.items()}}
]
files=['review-v0.4','evasion-family-v0.5-window180','paper-review-v1','throttle-isolation-v0.4']
report={'date':'2026-11-09','document_version':'1.6','review_kind':'Direct aggregation and matching of saved episode records; no new trading runs.','review_is_external_peer_review':False,'claims':claims,'trace_sha256':{str((data/n/'traces.jsonl.gz').relative_to(r)):hashlib.sha256((data/n/'traces.jsonl.gz').read_bytes()).hexdigest() for n in files},'remaining_scientific_limits':['One authored evasion family and reused seeds; no independent selected-window recall estimate.','Response-dependent wealth is conditional on the engineered price-response toggle.','No calibrated liquidity study or independent simulator validation.','Script objectives supply labels; no expert-adjudicated legality or intent labels.','No LLM trading, human-review workflow, or customer-demand findings.'],'assessment':'Suitable for consideration as a narrowly scoped exploratory simulation preprint after final artifact checks; neither external validation nor an acceptance guarantee.'}
(r/'paper/offline_study/research_review.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'claims_checked':len(claims),'matched_events':len(matched),'cadence_cells':len(cs),'split_probe_seeds':len(legacy),'throttle_gain':statistics.mean(diffs)},indent=2))
