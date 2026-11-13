"""Checks for the policy-gradient adversary."""
import json
import random
from pathlib import Path
import unittest

from paper_study import rl_adversary as R

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config.json').read_text())


class PolicyGradientTests(unittest.TestCase):
    def test_update_moves_probability_towards_rewarded_choice(self):
        logits = {'x': [0.0, 0.0, 0.0]}
        draws = [{'x': 0}, {'x': 1}, {'x': 2}, {'x': 0}]
        R.reinforce_step(logits, draws, [5.0, 0.0, 0.0, 5.0], 0.5)
        p = R.softmax(logits['x'])
        self.assertGreater(p[0], p[1])
        self.assertAlmostEqual(p[1], p[2])
        self.assertAlmostEqual(sum(p), 1.0)

    def test_equal_rewards_leave_policy_unchanged(self):
        logits = {'x': [0.1, 0.2]}
        R.reinforce_step(logits, [{'x': 0}, {'x': 1}], [3.0, 3.0], 0.5)
        self.assertEqual(logits, {'x': [0.1, 0.2]})

    def test_sampling_follows_probabilities(self):
        rng = random.Random(1)
        counts = [0, 0]
        for _ in range(4000):
            counts[R.sample(rng, [0.0, 1.0])] += 1
        self.assertAlmostEqual(counts[1] / 4000, R.softmax([0.0, 1.0])[1], delta=0.03)

    def test_small_search_is_deterministic_and_counts_samples(self):
        cell = {'control': 'aggregate_2x', 'response': 'fitted_msft'}
        run = {'name': 't', 'iterations': 2, 'seed': 5}
        spec = {**R.SPEC, 'batch': 2}
        stub = lambda jobs: [{'params': p, 'score': float(p['n_orders']), 'mean_D': 0.0, 'alert_rate': 0.0}
                             for _, _, p in jobs]
        a = R.search(BASE, cell, run, spec, stub)
        b = R.search(BASE, cell, run, spec, stub)
        self.assertEqual(a, b)
        self.assertEqual(a['samples'], 4)
        self.assertLessEqual(a['evaluations'], 4)
        self.assertEqual(a['train_score'], max(t['batch_max_score'] for t in a['trace']))


if __name__ == '__main__':
    unittest.main()
