import json
import unittest
from pathlib import Path

from pilot import throttle_isolation as ti
from pilot.revision import review_episode

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config.json').read_text())


class ThrottleIsolationTests(unittest.TestCase):
    def test_throttle_off_never_throttles(self):
        m = ti.episode(CONFIG, 4000, 'large_market_maker', 'aggregate_throttle_off')
        self.assertFalse(any(e.get('reason') == 'new_submission_throttle' for e in m.events))

    def test_throttle_off_keeps_aggregate_cap(self):
        m = ti.episode(CONFIG, 4000, 'split_6x10', 'aggregate_throttle_off')
        self.assertTrue(any(e.get('reason') == 'aggregate_resting_size_limit' for e in m.events))

    def test_other_gates_match_review_engine(self):
        for gate in ('off', 'legacy', 'aggregate'):
            a = ti.episode(CONFIG, 4001, 'large_market_maker', gate)
            b = review_episode(CONFIG, 4001, 'large_market_maker', True, gate)
            self.assertEqual(a.events, b.events, gate)

    def test_override_does_not_leak_into_shared_config(self):
        ti.make_market(CONFIG, 4000, 'aggregate_throttle_off')
        self.assertEqual(CONFIG['cancel_min_submissions'], 20)

    def test_history_counts_window(self):
        events = [{'time': 5, 'owner': 'subject', 'kind': 'submit'},
                  {'time': 6, 'owner': 'subject', 'kind': 'cancel'},
                  {'time': 70, 'owner': 'subject', 'kind': 'submit'}]
        h = ti.cancel_ratio_history(events, CONFIG)
        self.assertEqual(h[1], {'time': 60, 'submits': 1, 'cancels': 1, 'ratio': 1.0, 'throttle_armed': False})
        self.assertIsNone(h[0]['ratio'])

    def test_unknown_gate_rejected(self):
        with self.assertRaises(ValueError):
            ti.make_market(CONFIG, 4000, 'nope')


if __name__ == '__main__':
    unittest.main()
