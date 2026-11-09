import json
import unittest
from pathlib import Path

from pilot import window_sweep as ws
from pilot import evasion_family as ef
from pilot.run import subject_events

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config.json').read_text())
# Draw 9 of results/evasion-family-v0.4: slow 7x~11 single-level sequence that evades the 120 s window.
DRAW9 = {'n_orders': 7, 'lots_total': 80, 'price_spread_ticks': 0, 'submission_gap_s': 25,
         'lifetime_overlap': 'full', 'execution_delay_s': 81}
FAST = {'n_orders': 3, 'lots_total': 60, 'price_spread_ticks': 0, 'submission_gap_s': 0,
        'lifetime_overlap': 'full', 'execution_delay_s': 0}


def events(p, seed=5009):
    m, _ = ef.episode(CONFIG, seed, p, 'off', True)
    return subject_events(m.events)


class WindowSweepTests(unittest.TestCase):
    def test_hard_window_recovers_slow_draw_only_when_widened(self):
        ev = events(DRAW9)
        self.assertFalse(ws.hard_conduct(ev, CONFIG, 120))
        self.assertTrue(ws.hard_conduct(ev, CONFIG, 600))

    def test_hard_window_is_monotone_in_horizon(self):
        ev = events(DRAW9)
        flags = [ws.hard_conduct(ev, CONFIG, h) for h in ws.HORIZONS]
        self.assertEqual(flags, sorted(flags))

    def test_weighted_variant_catches_slow_draw_at_long_horizon(self):
        ev = events(DRAW9)
        self.assertFalse(ws.weighted_aggregate(ev, CONFIG, 120))
        self.assertTrue(ws.weighted_aggregate(ev, CONFIG, 600))

    def test_weighted_equals_full_weight_for_instant_sequence(self):
        ev = events(FAST, seed=5000)
        base = ws.hard_conduct(ev, CONFIG, 120)
        self.assertTrue(base)
        alerts = ws.weighted_aggregate(ev, CONFIG, 600)
        self.assertTrue(alerts)
        self.assertGreaterEqual(alerts[0]['weighted_lots'], CONFIG['spoof_size_multiple'] * CONFIG['background_depth'] * 0.9)

    def test_weight_is_zero_beyond_horizon(self):
        ev = events(DRAW9)
        # Ages in draw 9 are 89-239 s; at a 60 s horizon every order has weight 0.
        self.assertFalse(ws.weighted_aggregate(ev, CONFIG, 60))

    def test_conduct_ignores_excessive_cancels(self):
        self.assertFalse(ws.conduct([{'detector': 'excessive_cancels'}]))
        self.assertTrue(ws.conduct([{'detector': 'spoof_pattern'}]))


if __name__ == '__main__':
    unittest.main()
