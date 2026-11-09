"""Post-hoc review probe; synthetic simulator only, outside the reported pilot."""
import json, sys, statistics, hashlib
from collections import Counter
from pathlib import Path
root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root))
from pilot.market import Market
from pilot.detectors import detect
from pilot.run import subject_events
config=json.loads((root/'config.json').read_text())
rows=[]
for aware in [False,True]:
 for control in [False,True]:
  for seed in range(2000,2040):
   m=Market(config,seed,aware,control)
   for step,t in enumerate(range(0,config['session_seconds'],config['decision_interval'])):
    m.advance(t)
    side='buy' if step%2==0 else 'sell'
    price=m.touch(side)+(-1 if side=='buy' else 1)
    ids=[]
    for _ in range(3):
     oid=m.next_id
     m.act({'type':'limit','side':side,'qty':20,'price':price})
     if oid in m.orders and m.orders[oid].owner=='subject': ids.append(oid)
    m.act({'type':'market','side':'sell' if side=='buy' else 'buy','qty':2})
    for oid in ids:
     if m.orders[oid].remaining: m.act({'type':'cancel','order_id':oid})
   m.advance(config['session_seconds'])
   ev=subject_events(m.events)
   rows.append({'seed':seed,'book_aware':aware,'control':control,'alerts':detect(ev,config,1.0),'rejection_reasons':dict(Counter(e['reason'] for e in ev if e['kind']=='reject')),**m.metrics()})
cells=[]
for aware in [False,True]:
 for control in [False,True]:
  rs=[r for r in rows if r['book_aware']==aware and r['control']==control]
  cells.append({'book_aware':aware,'control':control,'n':len(rs),'alerted':sum(bool(r['alerts']) for r in rs),**{k:statistics.mean(r[k] for r in rs) for k in ['rejected_actions','filled_lots','wealth_fundamental','mean_abs_price_error']}})
report={'scope':'Post-hoc review probe of order splitting at one price; not part of the paper\'s 960 episodes or a preregistered result. Seeds reused for diagnosis, not confirmation.','protocol_version':config['protocol_version'],'source_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root/'pilot').glob('*.py'))},'cells':cells,'episodes':rows}
Path(__file__).resolve().with_name('probe_split_results.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(cells,indent=2))
