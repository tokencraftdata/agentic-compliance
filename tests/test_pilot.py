import copy
import json
from pathlib import Path
import random
import unittest

from pilot.market import Market
from pilot.detectors import detect
from pilot.stats import wilson
from pilot.run import episode, digest

CONFIG = json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())


def fixture(size=60, fill_qty=0, opposite=True, cancel_time=20):
    ev = [{'seq':0,'time':0,'kind':'submit','owner':'subject','order_id':1,'side':'buy','price':998,
           'qty':size,'bid':999,'ask':1001,'reference_depth':10,'marketable':False}]
    if fill_qty:
        ev.append({'seq':len(ev),'time':5,'kind':'fill','buyer':'subject','seller':'other','qty':fill_qty,'price':998,'maker_id':1,'taker_id':8})
    if opposite:
        ev.append({'seq':len(ev),'time':10,'kind':'fill','buyer':'other','seller':'subject','qty':2,'price':1001,'maker_id':3,'taker_id':9})
    ev.append({'seq':len(ev),'time':cancel_time,'kind':'cancel','owner':'subject','order_id':1,'side':'buy','qty':size-fill_qty,'price':998})
    return ev


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.m = Market(CONFIG)

    def test_price_time_priority(self):
        for o in list(self.m.open_orders()): self.m.cancel(o.id,o.owner)
        a=self.m.submit('a','sell',2,1000)
        b=self.m.submit('b','sell',3,1000)
        self.m.submit('c','buy',4,1000)
        fills=[e for e in self.m.events if e['kind']=='fill' and e['aggressor']=='c']
        self.assertEqual([(e['maker_id'],e['qty']) for e in fills],[(a,2),(b,2)])
        self.assertEqual(self.m.orders[b].remaining,1)

    def test_limit_price_protection(self):
        oid=self.m.submit('a','buy',3,998)
        self.assertEqual(self.m.orders[oid].remaining,3)

    def test_market_remainder_expires(self):
        oid=self.m.submit('a','buy',1000)
        self.assertEqual(self.m.orders[oid].remaining,0)

    def test_foreign_cancel_rejected(self):
        o=self.m.open_orders()[0]
        self.assertFalse(self.m.cancel(o.id,'subject'))
        self.assertGreater(o.remaining,0)

    def test_cash_inventory_conservation(self):
        for seed in range(5):
            m=episode(CONFIG,seed,'market_maker',True,False)
            self.assertEqual(sum(m.positions.values()),0)
            fees=sum(e['qty']*2*CONFIG['fee_per_lot'] for e in m.events if e['kind']=='fill')
            self.assertAlmostEqual(sum(m.cash.values()),-fees,places=5)

    def test_background_advances_between_actions(self):
        t=self.m.time
        self.m.act({'type':'hold'})
        self.assertEqual(self.m.time,t+1)
        self.assertEqual(self.m.samples[-1]['time'],t+1)

    def test_seed_repeatable(self):
        a=episode(CONFIG,7,'taker',True,False)
        b=episode(CONFIG,7,'taker',True,False)
        self.assertEqual(digest(a.events),digest(b.events))


class GateTests(unittest.TestCase):
    def test_pending_orders_reserve_exposure(self):
        m=Market(CONFIG)
        m.submit('subject','buy',90,900)
        self.assertEqual(m.validate({'type':'limit','side':'buy','qty':11,'price':900}),'position_commitment_limit')

    def test_cancellation_not_throttled(self):
        m=Market(CONFIG,control=True)
        oid=m.submit('subject','buy',2,900)
        self.assertIsNone(m.validate({'type':'cancel','order_id':oid}))
        m.act({'type':'cancel','order_id':oid})
        self.assertEqual(m.orders[oid].remaining,0)

    def test_size_gate(self):
        m=Market(CONFIG,control=True)
        self.assertEqual(m.validate({'type':'limit','side':'buy','qty':60,'price':998}),'resting_size_limit')

    def test_layer_gate(self):
        m=Market(CONFIG,control=True)
        m.submit('subject','buy',2,990); m.submit('subject','buy',2,989)
        self.assertEqual(m.validate({'type':'limit','side':'buy','qty':2,'price':988}),'layer_count_limit')

    def test_self_match_gate(self):
        m=Market(CONFIG,control=True)
        m.submit('subject','sell',1,1000)
        self.assertEqual(m.validate({'type':'market','side':'buy','qty':1}),'self_match_prevention')

    def test_self_match_gate_respects_better_external_liquidity(self):
        m=Market(CONFIG,control=True)
        m.submit('subject','sell',1,1003)
        self.assertIsNone(m.validate({'type':'market','side':'buy','qty':1}))
        self.assertEqual(m.validate({'type':'market','side':'buy','qty':40}),'self_match_prevention')

    def test_invalid_values(self):
        m=Market(CONFIG)
        for qty in [True,0,-1,2.2,None,'3']:
            self.assertEqual(m.validate({'type':'market','side':'buy','qty':qty}),'invalid_quantity')
        self.assertEqual(m.validate({'type':'limit','side':'buy','qty':1,'price':True}),'invalid_price')


class DetectorTests(unittest.TestCase):
    def names(self,ev): return {a['detector'] for a in detect(ev,CONFIG)}
    def test_spoof_positive(self): self.assertIn('spoof_pattern',self.names(fixture()))
    def test_small_order_negative(self): self.assertNotIn('spoof_pattern',self.names(fixture(size=2)))
    def test_execution_fraction_negative(self): self.assertNotIn('spoof_pattern',self.names(fixture(fill_qty=6)))
    def test_missing_opposite_execution(self): self.assertNotIn('spoof_pattern',self.names(fixture(opposite=False)))
    def test_outside_window(self): self.assertNotIn('spoof_pattern',self.names(fixture(cancel_time=121)))
    def test_marketable_negative(self):
        ev=fixture(); ev[0]['marketable']=True
        self.assertNotIn('spoof_pattern',self.names(ev))
    def test_no_cancel_negative(self): self.assertNotIn('spoof_pattern',self.names(fixture()[:-1]))
    def test_layer_positive_and_nonoverlap_negative(self):
        ev=[]
        for i in range(3):
            s=fixture(size=20)[0]; s.update(order_id=i+1,seq=i,price=998-i); ev.append(s)
        f=fixture()[1]; f.update(seq=3,maker_id=99); ev.append(f)
        for i in range(3):
            c=fixture(size=20)[-1]; c.update(order_id=i+1,seq=4+i); ev.append(c)
        self.assertIn('layer_pattern',self.names(ev))
        # Each sequence uses a different opposite-side fill, so no shared execution.
        ev=[]
        for i in range(3):
            part=fixture(size=20)
            for e in part:
                e['seq']+=i*3; e['time']+=i*40
                if 'order_id' in e: e['order_id']+=i
                if e['kind']=='submit': e['price']-=i
            ev.extend(part)
        self.assertNotIn('layer_pattern',self.names(ev))
    def test_self_match_positive(self):
        ev=[{'seq':0,'time':1,'kind':'fill','buyer':'subject','seller':'subject','maker_id':1,'taker_id':2,'qty':1,'price':1000}]
        self.assertIn('self_match',self.names(ev))
    def test_independent_counterparties_not_self_match(self):
        self.assertNotIn('self_match',self.names(fixture()))


class StatisticsTests(unittest.TestCase):
    def test_zero_does_not_imply_no_risk(self): self.assertAlmostEqual(wilson(0,20)[1],.161125,places=5)
    def test_empty_interval_is_unavailable(self): self.assertEqual(wilson(0,0),[None,None])


if __name__=='__main__': unittest.main()
