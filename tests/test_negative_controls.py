import json
import unittest
from pathlib import Path

from pilot import negative_controls as nc
from pilot.revision import review_episode

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config.json').read_text())


class NegativeControlTests(unittest.TestCase):
    def test_vwap_is_buy_only_and_never_shows_more_than_slice_cap(self):
        m = nc.episode(CONFIG, 6000, 'vwap_executor', 'off')
        subject = [e for e in m.events if e['kind'] == 'submit' and e.get('owner') == 'subject']
        self.assertTrue(subject)
        self.assertTrue(all(e['side'] == 'buy' and e['qty'] <= nc.SLICE_CAP for e in subject))
        self.assertLessEqual(nc.max_displayed(m.events), nc.SLICE_CAP)
        self.assertLessEqual(m.positions.get('subject', 0), nc.PARENT_LOTS)

    def test_vwap_cancels_and_replaces_each_step(self):
        m = nc.episode(CONFIG, 6000, 'vwap_executor', 'off')
        cancels = sum(e['kind'] == 'cancel' and e.get('owner') == 'subject' for e in m.events)
        self.assertGreater(cancels, 5)

    def test_iceberg_never_displays_more_than_show_size(self):
        m = nc.episode(CONFIG, 6000, 'iceberg', 'off')
        self.assertLessEqual(nc.max_displayed(m.events), nc.ICEBERG_SHOW)
        self.assertLessEqual(m.positions.get('subject', 0), nc.PARENT_LOTS)
        self.assertGreater(m.positions.get('subject', 0), 0)

    def test_makers_match_review_engine(self):
        for s in ('market_maker', 'large_market_maker'):
            for gate in ('off', 'aggregate'):
                a = nc.episode(CONFIG, 6001, s, gate)
                b = review_episode(CONFIG, 6001, s, True, gate)
                self.assertEqual(a.events, b.events, (s, gate))

    def test_patient_maker_rests_large_quotes_for_300s(self):
        m = nc.episode(CONFIG, 6000, 'patient_large_maker', 'off')
        subs = [e for e in m.events if e['kind'] == 'submit' and e.get('owner') == 'subject']
        self.assertEqual(sorted({e['time'] // 60 for e in subs}), [0, 5, 10])
        self.assertGreaterEqual(nc.max_displayed(m.events), 2 * nc.PATIENT_LOTS)
        ages = [e['time'] - next(s['time'] for s in subs if s['order_id'] == e['order_id'])
                for e in m.events if e['kind'] == 'cancel' and e.get('owner') == 'subject']
        self.assertTrue(ages and all(a >= 290 for a in ages))

    def test_max_displayed_accounts_for_fills_and_cancels(self):
        ev = [{'kind': 'submit', 'owner': 'subject', 'order_id': 1, 'qty': 10, 'marketable': False},
              {'kind': 'submit', 'owner': 'subject', 'order_id': 2, 'qty': 5, 'marketable': False},
              {'kind': 'fill', 'maker_id': 1, 'taker_id': 9, 'qty': 4},
              {'kind': 'cancel', 'owner': 'subject', 'order_id': 2}]
        self.assertEqual(nc.max_displayed(ev), 15)

    def test_unknown_strategy_and_gate_rejected(self):
        with self.assertRaises(ValueError):
            list(nc.control_actions(nc.make_market(CONFIG, 6000, 'off'), 'nope', 0, 12))
        with self.assertRaises(ValueError):
            nc.make_market(CONFIG, 6000, 'legacy')


if __name__ == '__main__':
    unittest.main()
