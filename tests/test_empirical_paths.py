"""Checks for the stylized-fact estimators and the real-path market."""
import json
from pathlib import Path
import unittest

from paper_study import adversary as A
from paper_study import empirical_paths as E

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config.json').read_text())
PARAMS = {'n_orders': 2, 'lots_total': 20, 'price_spread_ticks': 0, 'submission_gap_s': 0,
          'lifetime_overlap': 'full', 'execution_delay_s': 2}


class PathMarketTests(unittest.TestCase):
    def test_own_path_reproduces_original_market(self):
        cfg = A.config_for(BASE, A.lookup(A.CONTROLS, 'none'), A.lookup(A.RESPONSES, 'fitted_msft'), True)
        own = [f for f, _, _ in A.ResponseMarket(cfg, 9100, True, 'off').path]
        self.assertEqual(E.paired(cfg, 9100, 'off', PARAMS, own), A.paired(cfg, 9100, 'off', PARAMS))

    def test_path_must_start_at_base(self):
        cfg = A.config_for(BASE, A.lookup(A.CONTROLS, 'none'), A.lookup(A.RESPONSES, 'linear_k1'), True)
        with self.assertRaises(ValueError):
            E.PathMarket(cfg, 9100, True, 'off', [BASE['base_price'] + 1] * 740)

    def test_tick_path_and_windows(self):
        self.assertEqual(E.tick_path([10.2, 10.7, 11.4, 9.1], 1000), [1000, 1000, 1001, 999])
        spec = {**E.SPEC, 'window_seconds': 3}
        self.assertEqual(E.windows(list(range(10)), spec), [[0, 1, 2], [3, 4, 5], [6, 7, 8]])


class FactTests(unittest.TestCase):
    def test_constant_and_alternating_sessions(self):
        spec = {**E.SPEC, 'session_seconds': 120}
        flat = [[0.0] * 121] * 4
        self.assertEqual(E.facts(flat, spec)['zero_share_10s'], 1.0)
        zigzag = [[(t // 10) % 2 for t in range(121)] for _ in range(4)]
        self.assertLess(E.facts(zigzag, spec)['acf1_10s'], -0.9)


if __name__ == '__main__':
    unittest.main()
