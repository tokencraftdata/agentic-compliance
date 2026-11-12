"""Checks for the LLM-guided adversary, offline (mock proposer, no model calls)."""
import json
from pathlib import Path
import unittest

from paper_study import adversary as A
from paper_study import llm_adversary as L

ROOT = Path(__file__).resolve().parents[1]
BASE = json.loads((ROOT / 'config.json').read_text())
PARAMS = {'n_orders': 3, 'lots_total': 30, 'price_spread_ticks': 2, 'submission_gap_s': 1,
          'lifetime_overlap': 'full', 'execution_delay_s': 2}
SMALL = {**L.SPEC, 'rounds': 2, 'candidates_per_round': 2}
CELL = {'control': 'aggregate_2x', 'response': 'fitted_msft'}


def serial(jobs):
    return [L.evaluate(j) for j in jobs]


class LlmAdversaryTests(unittest.TestCase):
    def test_paired_detail_matches_adversary(self):
        cfg, gate = L.cell_config(BASE, {'control': 'none', 'response': 'linear_k1'})
        mine = L.paired_detail(cfg, 9000, gate, PARAMS)
        ref = A.paired(cfg, 9000, gate, PARAMS)
        self.assertEqual({k: mine[k] for k in ('seed', 'D', 'alert', 'filled_lots', 'rejected')},
                         {k: ref[k] for k in ('seed', 'D', 'alert', 'filled_lots', 'rejected')})

    def test_score_matches_adversary_objective(self):
        cfg, gate = L.cell_config(BASE, CELL)
        mine = L.evaluate((cfg, gate, PARAMS, [9000, 9001], 100.0))
        value, _ = A.evaluate(cfg, [9000, 9001], gate, PARAMS, 100.0)
        self.assertEqual(mine['score'], value)

    def test_parse(self):
        good = dict(PARAMS)
        text = 'Here: ' + json.dumps({'analysis': 'x', 'candidates': [good, {**good, 'n_orders': 99}, good]})
        cands, problems = L.parse(text, 2)
        self.assertEqual(cands, [good])
        self.assertEqual(problems, ['invalid_candidate', 'extra_candidates_ignored'])
        self.assertEqual(L.parse('no json here', 2), ([], ['unparseable_reply']))
        self.assertEqual(L.parse(json.dumps({'candidates': [{**good, 'n_orders': 3.0}]}), 2)[0], [])

    def test_mock_search_replays_exactly(self):
        found = L.search(BASE, CELL, SMALL, L.MockProposer(SMALL), serial)
        self.assertLessEqual(found['evaluations'], 4)
        replies = [r['reply'] for r in found['rounds']]
        again = L.search(BASE, CELL, SMALL, None, serial, replies)
        self.assertEqual(again, found)
        self.assertEqual(found['train_score'], max(h['score'] for h in found['history']))

    def test_repeats_use_no_evaluation(self):
        reply = {'status': 'ok', 'text': json.dumps({'candidates': [PARAMS, PARAMS]})}
        found = L.search(BASE, CELL, SMALL, None, serial, [reply, reply])
        self.assertEqual(found['evaluations'], 1)
        self.assertEqual([r['problems'] for r in found['rounds']], [['repeat_candidate'], ['repeat_candidate'] * 2])

    def test_request_fits_input_bound(self):
        cfg, gate = L.cell_config(BASE, CELL)
        row = L.evaluate((cfg, gate, PARAMS, [9000], 100.0))
        row['rejection_reasons'] = {'aggregate_resting_size_limit': 40, 'layer_count_limit': 40,
                                    'new_submission_throttle': 40, 'self_match_prevention': 40}
        row['paths_with_alert_by_detector'] = {'aggregate_spoof_pattern': 10, 'layer_pattern': 10,
                                               'self_match': 10, 'spoof_pattern': 10}
        history = [row] * (L.SPEC['rounds'] * L.SPEC['candidates_per_round'])
        request = L.request_for(CELL, history, 19, L.SPEC)
        self.assertLess(len(L.SYSTEM) + len(L.canonical(request)), L.INPUT_CHAR_LIMIT)


if __name__ == '__main__':
    unittest.main()
