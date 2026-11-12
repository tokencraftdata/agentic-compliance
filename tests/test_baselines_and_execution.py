"""Checks for the execution-quality measures and the baseline detectors."""
import json
from pathlib import Path
import unittest

from paper_study import adversary as A
from paper_study import baselines as B
from paper_study import execution_quality as E

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config.json').read_text())


def condition(control='none', response='fitted_msft', throttle=False):
    c = A.lookup(A.CONTROLS, control)
    return A.config_for(BASE, c, A.lookup(A.RESPONSES, response), throttle), c['gate']


class ExecutionQualityTests(unittest.TestCase):
    def test_episode_is_deterministic(self):
        cfg, gate = condition()
        a = E.episode(cfg, 9300, gate, 'iceberg', 60)
        b = E.episode(cfg, 9300, gate, 'iceberg', 60)
        self.assertEqual(a, b)

    def test_executor_measures_are_consistent(self):
        cfg, gate = condition()
        row = E.episode(cfg, 9300, gate, 'vwap_executor', 60)
        self.assertTrue(0 <= row['fill_rate'] <= 1)
        self.assertLessEqual(row['time_to_half_s'], cfg['session_seconds'])
        if row['fill_rate'] < 0.5:
            self.assertEqual(row['time_to_half_s'], cfg['session_seconds'])

    def test_maker_markout_nets_out_of_edge(self):
        cfg, gate = condition()
        row = E.episode(cfg, 9300, gate, 'market_maker', 60)
        self.assertGreater(row['filled_lots'], 0)
        self.assertGreaterEqual(row['mean_abs_inventory'], 0)

    def test_ungated_arm_is_first(self):
        self.assertEqual(E.ARMS[0], ('none', False))


class BaselineTests(unittest.TestCase):
    def test_default_execution_size_matches_adversary(self):
        cfg, gate = condition('aggregate_2x', 'linear_k1', True)
        params = {'n_orders': 2, 'lots_total': 40, 'price_spread_ticks': 1, 'submission_gap_s': 10,
                  'lifetime_overlap': 'full', 'execution_delay_s': 0}
        ours = B.run_schedule(A.ResponseMarket(cfg, 9100, True, gate), params, cfg)
        theirs = A.run_schedule(A.ResponseMarket(cfg, 9100, True, gate), params, cfg)
        self.assertEqual(A.digest(ours.events), A.digest(theirs.events))

    def test_quantile_threshold_allows_target_share(self):
        values = list(range(100))
        t = B.quantile_threshold(values, 0.05)
        self.assertEqual(sum(v > t for v in values), 5)
        self.assertEqual(B.quantile_threshold([1, 2, 3], 0.05), 3)

    def test_logistic_separates_separable_data(self):
        rows = [{k: (1.0 if i % 2 else 0.0) + 0.01 * i for k in B.FEATURES} for i in range(20)]
        labels = [i % 2 for i in range(20)]
        spec = {**B.SPEC, 'logistic': {**B.SPEC['logistic'], 'iterations': 500}}
        model = B.fit_logistic(rows, labels, spec)
        scores = [B.logit(model, r) for r in rows]
        self.assertGreater(min(s for s, y in zip(scores, labels) if y), max(s for s, y in zip(scores, labels) if not y))

    def test_features_of_legitimate_executor(self):
        cfg, gate = condition()
        row = B.observe(cfg, gate, 9000, B.SPEC, strategy='iceberg')
        self.assertEqual(row['aggressor_share'], 0.0)
        self.assertTrue(0 <= row['cancel_ratio'] <= 1)
        self.assertGreater(row['otr'], 0)


if __name__ == '__main__':
    unittest.main()
