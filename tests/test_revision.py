import copy
import json
from pathlib import Path
import unittest
from pilot.market import Market
from pilot.detectors import detect
from pilot.revision import AggregateMarket, extended_detect, review_episode, make_market
from pilot.run import subject_events

CONFIG=json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text())


def split_fixture(qty=20, own_fills=0):
    ev=[]
    for oid in (1,2):
        ev.append(dict(seq=len(ev),time=0,kind='submit',owner='subject',order_id=oid,
                       side='buy',price=998,qty=qty,bid=999,ask=1001,reference_depth=10,marketable=False))
    if own_fills:
        for oid in (1,2):
            ev.append(dict(seq=len(ev),time=1,kind='fill',buyer='subject',seller='other',
                           qty=own_fills,price=998,maker_id=oid,taker_id=90+oid))
    ev.append(dict(seq=len(ev),time=2,kind='fill',buyer='other',seller='subject',qty=2,price=1001,maker_id=99,taker_id=100))
    for oid in (1,2):
        ev.append(dict(seq=len(ev),time=3,kind='cancel',owner='subject',order_id=oid,qty=qty-own_fills,side='buy',price=998))
    return ev


class RevisionTests(unittest.TestCase):
    def names(self,ev):return {x['detector'] for x in extended_detect(ev,CONFIG)}

    def test_same_price_split_exposes_original_blind_spot(self):
        for strategy in ('split_3x20','split_6x10'):
            m=review_episode(CONFIG,2000,strategy,True,'off')
            self.assertFalse(detect(subject_events(m.events),CONFIG))
            self.assertIn('aggregate_spoof_pattern',self.names(subject_events(m.events)))
            self.assertGreater(m.metrics()['mean_abs_price_error'],0)

    def test_aggregate_gate_removes_tested_split_price_effect_without_stopping_execution(self):
        for strategy in ('split_3x20','split_6x10'):
            for seed in (2000,2001,2002):
                m=review_episode(CONFIG,seed,strategy,True,'aggregate')
                self.assertEqual(m.metrics()['mean_abs_price_error'],0)
                self.assertEqual(m.metrics()['filled_lots'],24)

    def test_cap_combines_different_prices(self):
        m=AggregateMarket(CONFIG,seed=2000,control=True)
        m.submit('subject','buy',10,998)
        self.assertEqual(m.validate(dict(type='limit',side='buy',qty=11,price=997)),'aggregate_resting_size_limit')

    def test_cap_combines_same_price_and_allows_boundary(self):
        m=AggregateMarket(CONFIG,seed=2000,control=True)
        m.submit('subject','buy',10,998)
        self.assertIsNone(m.validate(dict(type='limit',side='buy',qty=10,price=998)))
        self.assertEqual(m.validate(dict(type='limit',side='buy',qty=11,price=998)),'aggregate_resting_size_limit')

    def test_cancellation_releases_capacity(self):
        m=AggregateMarket(CONFIG,seed=2000,control=True)
        oid=m.submit('subject','buy',20,998)
        self.assertIsNone(m.validate(dict(type='cancel',order_id=oid)))
        m.cancel(oid,'subject')
        self.assertIsNone(m.validate(dict(type='limit',side='buy',qty=20,price=997)))

    def test_opposite_sides_not_combined(self):
        m=AggregateMarket(CONFIG,seed=2000,control=True)
        m.submit('subject','sell',20,1002)
        self.assertIsNone(m.validate(dict(type='limit',side='buy',qty=20,price=998)))

    def test_detector_uses_remaining_quantity_at_execution(self):
        self.assertIn('aggregate_spoof_pattern',self.names(split_fixture(qty=15)))
        self.assertNotIn('aggregate_spoof_pattern',self.names(split_fixture(qty=15,own_fills=1)))

    def test_detector_requires_shared_execution(self):
        ev=[]
        for i in range(2):
            part=split_fixture(qty=20)
            part=[e for e in part if e.get('order_id')!=2]
            for e in part:
                e['seq']=len(ev);e['time']+=i*10
                if 'order_id' in e:e['order_id']+=i*10
                ev.append(e)
        self.assertNotIn('aggregate_spoof_pattern',self.names(ev))

    def test_detector_does_not_pool_distant_orders(self):
        ev=split_fixture();ev[1]['price']=990
        self.assertNotIn('aggregate_spoof_pattern',self.names(ev))

    def test_detector_requires_cancellation_and_opposite_execution(self):
        ev=split_fixture()
        self.assertNotIn('aggregate_spoof_pattern',self.names([e for e in ev if e['kind']!='cancel']))
        self.assertNotIn('aggregate_spoof_pattern',self.names([e for e in ev if e['kind']!='fill']))

    def test_unknown_gate_rejected(self):
        with self.assertRaises(ValueError):make_market(CONFIG,0,True,'typo')


if __name__=='__main__':unittest.main()
