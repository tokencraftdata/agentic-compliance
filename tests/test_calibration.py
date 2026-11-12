"""Checks for the LOBSTER imbalance-response estimator."""
import tempfile
from pathlib import Path
import unittest

from paper_study import calibration as C


class CalibrationTests(unittest.TestCase):
    def test_ols_recovers_a_known_line(self):
        xs = [-1.0, -0.5, 0.0, 0.5, 1.0]
        slope, intercept, origin = C.ols(xs, [0.2 * x + 0.1 for x in xs])
        self.assertAlmostEqual(slope, 0.2)
        self.assertAlmostEqual(intercept, 0.1)
        self.assertAlmostEqual(origin, 0.2)

    def test_snapshot_keeps_last_state_in_each_second(self):
        with tempfile.TemporaryDirectory() as d:
            msg, book = Path(d) / 'm.csv', Path(d) / 'b.csv'
            msg.write_text('34200.1,1,1,10,1000100,1\n34200.9,1,2,10,1000100,1\n34201.5,7,0,0,-1,-1\n')
            levels = lambda bid: ','.join(f'{1000200 + 100 * k},10,{1000100 - 100 * k},{bid}' for k in range(3))
            book.write_text(levels(10) + '\n' + levels(30) + '\n' + levels(30) + '\n')
            grid = C.snapshots(msg, book)
        self.assertEqual(list(grid), [34201])           # the halt row is skipped
        mid, half, i3, i1 = grid[34201]
        self.assertEqual((mid, half), (1000150.0, 50.0))
        self.assertAlmostEqual(i3, (90 - 30) / 120)      # three bid levels of 30 vs ask levels of 10
        self.assertAlmostEqual(i1, (30 - 10) / 40)


if __name__ == '__main__':
    unittest.main()
