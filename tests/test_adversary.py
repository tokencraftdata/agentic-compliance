"""Checks for the adaptive adversary and the linear-probability response."""
import json
from pathlib import Path
import unittest

from paper_study import adversary as A
from paper_study.sensitivity import SensitivityMarket, config_for as sensitivity_config, execute

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config.json').read_text())


class AdversaryTests(unittest.TestCase):
    def test_step_mode_reproduces_sensitivity_engine(self):
        condition = {'horizon': 720, 'window': 1800, 'cap': 2, 'threshold': 0.2, 'throttle': False,
                     'aware': True, 'draw': None, 'cohort': 'x', 'strategy': 'split_6x10',
                     'gate': 'aggregate', 'seed': 8000}
        reference = execute(sensitivity_config(BASE, condition), condition, [])
        cfg = A.config_for(BASE, A.lookup(A.CONTROLS, 'aggregate_2x'), A.lookup(A.RESPONSES, 'step_t0.2'), False)
        m = A.run_script(A.ResponseMarket(cfg, 8000, True, 'aggregate'), 'split_6x10', cfg)
        self.assertEqual(A.digest(m.events), A.digest(reference.events))

    def test_v24_cap_result_is_reproduced(self):
        for response, expected in [('step_t0.4', 0.0), ('step_t0.2', 48.0)]:
            cfg = A.config_for(BASE, A.lookup(A.CONTROLS, 'aggregate_2x'), A.lookup(A.RESPONSES, response), False)
            row = A.paired(cfg, 8000, 'aggregate', strategy='split_6x10')
            self.assertEqual(row['D'], expected)
            self.assertFalse(row['alert'])

    def test_linear_response_moves_with_probability_equal_to_imbalance(self):
        cfg = {**BASE, 'response_mode': 'linear', 'response_kappa': 1, 'cancel_min_submissions': 10**9}
        m = A.ResponseMarket(cfg, 5000, True, 'off')
        m.uniforms = [0.2] * len(m.uniforms)
        m.path[0] = (m.fundamental, 'buy', 0)
        for o in list(m.open_orders()):
            m.cancel(o.id, o.owner)
        m.background()
        m.submit('subject', 'buy', 20, 900)   # imbalance 20/80 = 0.25 > 0.2
        m.background()
        self.assertEqual(m.response_inputs[-1][1:], (0.25, 1))
        m.uniforms = [0.3] * len(m.uniforms)  # 0.3 > 0.25: no move
        m.background()
        self.assertEqual(m.response_inputs[-1][2], 0)

    def test_unresponsive_market_never_shifts(self):
        cfg = A.config_for(BASE, A.lookup(A.CONTROLS, 'none'), A.lookup(A.RESPONSES, 'linear_k2'), False)
        m = A.run_script(A.ResponseMarket(cfg, 9000, False, 'off'), 'split_6x10', cfg)
        self.assertTrue(all(shift == 0 for _, _, shift in m.response_inputs))

    def test_search_is_deterministic_and_never_uses_test_seeds(self):
        spec = {**A.SPEC, 'random_candidates': 2, 'local_steps': 1, 'train_seeds': [9000]}
        control, response = A.lookup(A.CONTROLS, 'aggregate_2x'), A.lookup(A.RESPONSES, 'linear_k1')
        first, second = A.search(BASE, control, response, spec), A.search(BASE, control, response, spec)
        self.assertEqual(first, second)
        self.assertFalse(set(A.SPEC['train_seeds']) & set(A.SPEC['test_seeds']))
        self.assertFalse(set(A.SPEC['test_seeds']) & set(A.SPEC['confirm_seeds']))

    def test_bootstrap_interval_brackets_constant_mean(self):
        self.assertEqual(A.bootstrap([3.0] * 20, 200, 1), [3.0, 3.0])


if __name__ == '__main__':
    unittest.main()
