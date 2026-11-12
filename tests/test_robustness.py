"""Checks for the response-window market and the display-gain counterfactual."""
import json
from pathlib import Path
import unittest

from paper_study import adversary as A
from paper_study import robustness as R

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config.json').read_text())
PARAMS = {'n_orders': 2, 'lots_total': 50, 'price_spread_ticks': 4, 'submission_gap_s': 1,
          'lifetime_overlap': 'staggered', 'execution_delay_s': 2}


class WindowTests(unittest.TestCase):
    def test_no_window_matches_original_market(self):
        cfg = R.config(BASE, 'none', 'fitted_msft', True, None)
        ours = A.run_schedule(R.WindowMarket(cfg, 9100, True, 'off'), PARAMS, cfg)
        theirs = A.run_schedule(R.WindowMarket.__mro__[1](cfg, 9100, True, 'off'), PARAMS, cfg)
        self.assertEqual(A.digest(ours.events), A.digest(theirs.events))

    def test_window_ignores_far_depth(self):
        cfg = R.config(BASE, 'none', 'linear_k1', True, 3)
        m = R.WindowMarket(cfg, 9100, True, 'off')
        m.advance(1)
        before = m.windowed('buy', 3)
        m.act({'type': 'limit', 'side': 'buy', 'qty': 40, 'price': m.touch('buy') - 5})
        self.assertEqual(m.windowed('buy', 3), before)
        m.act({'type': 'limit', 'side': 'buy', 'qty': 7, 'price': m.touch('buy')})
        self.assertEqual(m.windowed('buy', 3), before + 7)

    def test_execution_only_keeps_market_orders(self):
        cfg = R.config(BASE, 'none', 'fitted_msft', True, 3)
        bare = R.run_execution_only(R.WindowMarket(cfg, 9100, True, 'off'), PARAMS, cfg)
        sizes = {o.qty for o in bare.orders.values() if o.owner == 'subject'}
        self.assertEqual(sizes, {2})   # only the two-lot executions, no displayed orders
        self.assertEqual(bare.metrics()['filled_lots'], 24)

    def test_tick_mapping_scales(self):
        scales = {r['name']: r['scale'] for r in R.TICK_RESPONSES}
        self.assertAlmostEqual(scales['ticks_aapl'], 0.133882, places=5)
        self.assertAlmostEqual(scales['ticks_msft'], 0.239456, places=5)


if __name__ == '__main__':
    unittest.main()
