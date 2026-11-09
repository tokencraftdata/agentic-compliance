import copy
import json
import unittest
from pathlib import Path
from paper_study.evaluate import max_displayed_per_side, matched_effect, maker_episode
from pilot.negative_controls import episode

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'results/evasion-family-v0.5-window180/config.json').read_text())


class PaperStudyTests(unittest.TestCase):
    def test_sides_are_not_combined(self):
        events = [{'kind': 'submit', 'owner': 'subject', 'order_id': 1, 'side': 'buy', 'qty': 16, 'marketable': False},
                  {'kind': 'submit', 'owner': 'subject', 'order_id': 2, 'side': 'sell', 'qty': 16, 'marketable': False},
                  {'kind': 'fill', 'maker_id': 1, 'taker_id': 9, 'qty': 8},
                  {'kind': 'submit', 'owner': 'subject', 'order_id': 3, 'side': 'buy', 'qty': 16, 'marketable': False}]
        self.assertEqual(max_displayed_per_side(events), 24)

    def test_positive_absolute_wealth_is_not_response_advantage(self):
        r = matched_effect(3.7, 3.7, 3.7, 3.7, False, 0)
        self.assertFalse(r['positive_gated_response_advantage_undetected'])

    def test_partial_rejection_does_not_hide_economic_residual(self):
        r = matched_effect(10, 0, 5, 1, False, 7)
        self.assertTrue(r['positive_gated_response_advantage_undetected'])
        self.assertEqual(r['response_advantage_gate'], 4)

    def test_gated_counterfactual_removes_fee_saving_confound(self):
        r = matched_effect(10, 1, 1.4, 1.4, False, 3)
        self.assertEqual(r['response_advantage_gate'], 0)

    def test_original_patient_maker_semantics_preserved(self):
        for gate in ('off', 'aggregate'):
            self.assertEqual(maker_episode(CONFIG, 6000, gate, 300).events,
                             episode(CONFIG, 6000, 'patient_large_maker', gate).events)

    def test_unknown_condition_rejected(self):
        with self.assertRaises(ValueError):
            maker_episode(CONFIG, 6000, 'legacy', 180)


if __name__ == '__main__':
    unittest.main()
