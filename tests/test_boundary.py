"""Check exact-time scheduling and evidence accounting for boundary experiments."""
import json
from pathlib import Path
import unittest

from paper_study.boundary import SPEC, conditions, maker_episode
from paper_study.evaluate import maker_episode as original_maker
from paper_study.reporting import describe, rejection_categories
from pilot.run import digest

ROOT = Path(__file__).resolve().parents[1]


class BoundaryTests(unittest.TestCase):
    def config(self):
        return json.loads((ROOT/'results/paper-review-v1/config.json').read_text())

    def test_cadence_180_preserves_original_action_schedule(self):
        config = self.config()
        self.assertEqual(digest(maker_episode(config, 6000, 180).events),
                         digest(original_maker(config, 6000, 'off', 180).events))

    def test_off_grid_cadences_are_not_rounded_to_decision_interval(self):
        for cadence in (170, 190):
            m = maker_episode(self.config(), 6000, cadence)
            attempts = {e['time'] for e in m.events if e['kind'] == 'attempt' and e['action']['type'] != 'hold'}
            self.assertTrue(set(range(0, 720, cadence)) <= attempts)
            self.assertNotIn(180, attempts)

    def test_rejects_overlapping_batches(self):
        with self.assertRaisesRegex(ValueError, 'overlapped'):
            maker_episode(self.config(), 6000, 119)

    def test_plan_has_all_pairs_and_distinct_conditions(self):
        cells = list(conditions({'spec': SPEC}))
        self.assertEqual(len(cells), 560)
        self.assertEqual(len({json.dumps(c, sort_keys=True) for c in cells}), 560)
        paired = {}
        for c in cells:
            if c['cohort'] == 'cap_boundary':
                key = json.dumps({k: v for k, v in c.items() if k != 'aware'}, sort_keys=True)
                paired.setdefault(key, set()).add(c['aware'])
        self.assertEqual(len(paired), 240)
        self.assertTrue(all(v == {True, False} for v in paired.values()))

    def test_rejection_categories_reconcile_and_retain_unknown_reasons(self):
        self.assertEqual(rejection_categories({'resting_size_limit': 2, 'aggregate_resting_size_limit': 3,
            'new_submission_throttle': 7, 'position_commitment_limit': 1, 'unknown_or_inactive_order': 4,
            'future_reason': 1}), {'cap': 5, 'throttle': 7, 'position': 1, 'other': 5, 'total': 18})

    def test_spread_is_observed_range_not_interval(self):
        self.assertEqual(describe([0, 10, 20]), {'n': 3, 'mean': 10, 'min': 0, 'max': 20, 'sd': 10.0})
        with self.assertRaises(ValueError):
            describe([])


if __name__ == '__main__':
    unittest.main()
