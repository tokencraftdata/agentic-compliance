import json
import unittest
from pathlib import Path

from pilot import evasion_family as ef

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config.json').read_text())


def params(**kw):
    p = {'n_orders': 3, 'lots_total': 60, 'price_spread_ticks': 0, 'submission_gap_s': 0,
         'lifetime_overlap': 'full', 'execution_delay_s': 0}
    p.update(kw)
    return p


class EvasionFamilyTests(unittest.TestCase):
    def test_draws_are_seeded_and_in_space(self):
        a, b = ef.draw_vectors(), ef.draw_vectors()
        self.assertEqual(a, b)
        self.assertEqual(len(a), 40)
        for v in a:
            for k, space in ef.SPACES.items():
                self.assertIn(v[k], space)

    def test_schedule_full_matches_split_probe_shape(self):
        events, dur = ef.schedule(params())
        kinds = [a[0] for _, a in events]
        self.assertEqual(kinds, ['limit'] * 3 + ['market'] + ['cancel'] * 3)
        self.assertEqual([a[2] for _, a in events if a[0] == 'limit'], [20, 20, 20])
        self.assertEqual([a[3] for _, a in events if a[0] == 'limit'], [1, 1, 1])
        self.assertEqual(dur, 7)

    def test_schedule_lots_and_spread(self):
        events, _ = ef.schedule(params(n_orders=4, lots_total=40 + 2, price_spread_ticks=3))
        limits = [a for _, a in events if a[0] == 'limit']
        self.assertEqual([a[2] for a in limits], [11, 11, 10, 10])
        self.assertEqual([a[3] for a in limits], [1, 2, 3, 4])

    def test_disjoint_has_one_live_order_at_execution(self):
        events, _ = ef.schedule(params(lifetime_overlap='disjoint', submission_gap_s=5))
        before = [a for t, a in events if t < [t for t, a in events if a[0] == 'market'][0]]
        live = {a[1] for a in before if a[0] == 'limit'} - {a[1] for a in before if a[0] == 'cancel'}
        self.assertEqual(live, {2})

    def test_staggered_spreads_cancels(self):
        events, _ = ef.schedule(params(lifetime_overlap='staggered', submission_gap_s=10))
        cancels = [t for t, a in events if a[0] == 'cancel']
        self.assertEqual([b - a for a, b in zip(cancels, cancels[1:])], [11, 11])

    def test_cycles_fit_session_and_times_monotone(self):
        m, cycles = ef.episode(CONFIG, 5000, params(submission_gap_s=30, execution_delay_s=90, n_orders=8), 'off', True)
        self.assertGreaterEqual(cycles, 1)
        subject = [e for e in m.events if e.get('owner') == 'subject' and e['kind'] in ('submit', 'cancel')]
        self.assertTrue(all(e['time'] <= CONFIG['session_seconds'] for e in subject))
        self.assertFalse(m.open_orders('subject'), 'every cycle must cancel its own orders')

    def test_full_split_reproduces_v04_split_probe_result(self):
        m, _ = ef.episode(CONFIG, 5000, params(), 'off', True)
        self.assertTrue(m.metrics()['wealth_fundamental'] > 0)
        m2, _ = ef.episode(CONFIG, 5000, params(), 'aggregate', True)
        self.assertTrue(any(e.get('reason') == 'aggregate_resting_size_limit' for e in m2.events))

    def test_unknown_gate_rejected(self):
        with self.assertRaises(ValueError):
            ef.make_market(CONFIG, 5000, 'legacy', True)


if __name__ == '__main__':
    unittest.main()
