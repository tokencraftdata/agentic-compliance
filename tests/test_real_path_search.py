"""Checks for the adversary search on real price paths, using synthetic paths in place of LOBSTER data."""
import json
from pathlib import Path
import unittest

from paper_study import adversary as A
from paper_study import real_path_search as R

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config.json').read_text())
CONTROL, RESPONSE = A.lookup(A.CONTROLS, 'none'), A.lookup(A.RESPONSES, 'fitted_msft')
SMALL = {**A.SPEC, 'random_candidates': 3, 'local_steps': 3, 'search_seed': 7}


def own_paths(cfg, n):
    return [[f for f, _, _ in A.ResponseMarket(cfg, R.SPEC['seed_base'] + k, True, 'off').path] for k in range(n)]


class RealPathSearchTests(unittest.TestCase):
    def test_folds_score_every_window_once(self):
        folds = [R.fold_of(k, R.SPEC) for k in range(30)]
        self.assertEqual(sorted(folds.count(f) for f in range(R.SPEC['folds'])), [10, 10, 10])

    def test_search_on_own_paths_matches_adversary_search(self):
        cfg = A.config_for(BASE, CONTROL, RESPONSE, True)
        paths = own_paths(cfg, 3)
        spec = {**R.SPEC, 'search_seed': 7}
        best, score, log = R.search(cfg, CONTROL['gate'], paths, [0, 1, 2], 'none|fitted_msft', spec, SMALL)
        ref = A.search(BASE, CONTROL, RESPONSE, {**SMALL, 'train_seeds': [9100, 9101, 9102]})
        self.assertEqual((best, score, log), (ref['best_params'], ref['train_score'], ref['log']))

    def test_held_out_job_scores_both_policies_on_fold_windows(self):
        cfg = A.config_for(BASE, CONTROL, RESPONSE, True)
        paths = own_paths(cfg, 3)
        params = {'n_orders': 2, 'lots_total': 20, 'price_spread_ticks': 0, 'submission_gap_s': 0,
                  'lifetime_overlap': 'full', 'execution_delay_s': 2}
        found = {'ticker': 'MSFT', 'response': 'fitted_msft', 'control': 'none', 'fold': 1, 'best_params': params}
        rows = R._test_job((BASE, R.SPEC, found, params, paths))
        self.assertEqual([r['window'] for r in rows], [1])
        self.assertEqual(rows[0]['real'], rows[0]['synthetic'])
        self.assertEqual(rows[0]['real']['D'], A.paired(cfg, 9101, 'off', params)['D'])


if __name__ == '__main__':
    unittest.main()
