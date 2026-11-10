"""Integration checks for the two deliberate departures from the frozen engine."""
import json
from pathlib import Path
import unittest

from paper_study.sensitivity import SensitivityMarket

ROOT = Path(__file__).resolve().parents[1]


class SensitivityTests(unittest.TestCase):
    def config(self, **changes):
        return {**json.loads((ROOT / 'config.json').read_text()),
                'cancel_min_submissions': 10**9, **changes}

    def test_aggregate_multiple_does_not_relax_individual_limit(self):
        m = SensitivityMarket(self.config(aggregate_size_multiple=6), 5000, False, 'aggregate')
        self.assertEqual(m.validate({'type':'limit','side':'buy','qty':21,'price':900}), 'resting_size_limit')
        for _ in range(6):
            m.act({'type':'limit','side':'buy','qty':10,'price':900})
        self.assertEqual(sum(o.remaining for o in m.open_orders('subject','buy')), 60)
        self.assertEqual(m.validate({'type':'limit','side':'buy','qty':10,'price':900}), 'aggregate_resting_size_limit')

    def test_response_threshold_is_strict_and_parameterized(self):
        for threshold, expected_shift in [(0.25,0), (0.249,2)]:
            m = SensitivityMarket(self.config(response_threshold=threshold), 5000, True, 'aggregate')
            m.path[0] = (m.fundamental, 'buy', 0)
            for o in list(m.open_orders()):
                m.cancel(o.id, o.owner)
            m.background()
            m.submit('subject','buy',20,900)
            m.background()
            self.assertEqual(m.response_inputs[-1][1:], (0.25, expected_shift))
            self.assertEqual(m.samples[-1]['mid']-m.fundamental, expected_shift)


if __name__ == '__main__':
    unittest.main()
