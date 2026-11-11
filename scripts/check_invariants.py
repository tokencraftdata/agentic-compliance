"""Exhaustive finite-state exposure check. Not an unbounded theorem proof."""
import json
from pathlib import Path
from pilot.market import Market, Order

config=json.loads(Path('config.json').read_text())
config['position_limit']=3
checked=0
states=0
for pos in range(-3,4):
    for buys in range(7):
        for sells in range(7):
            if pos+buys>3 or pos-sells < -3: continue
            states+=1
            for side in ['buy','sell']:
                for qty in range(1,7):
                    m=Market(config)
                    m.positions['subject']=pos
                    m.orders={100:Order(100,'subject','buy',990,buys,buys,0),101:Order(101,'subject','sell',1010,sells,sells,0)}
                    m.active_ids=set(m.orders)
                    accepted=m.validate({'type':'limit','side':side,'qty':qty,'price':990 if side=='buy' else 1010}) is None
                    expected=pos+buys+qty<=3 if side=='buy' else pos-sells-qty>=-3
                    if accepted != expected: raise AssertionError((pos,buys,sells,side,qty))
                    checked+=1
print(json.dumps({'scope':'position cap 3; integer inventory -3..3; pending buy/sell lots 0..6; new lots 1..6',
                  'states':states,'transitions_checked':checked,'failures':0,
                  'claim':'Acceptance equals the worst-case exposure predicate over this finite domain only.'},indent=2))
