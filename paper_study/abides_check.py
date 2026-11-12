"""Robustness check in ABIDES: does displayed, cancelled depth pay when prices respond endogenously?

TradeGauntlet imposes its price response. ABIDES (Byrd, Hybinette and Balch,
2020) has none: prices move only because its noise, value, momentum and
adaptive market-making agents react to the book and to a mean-reverting
fundamental. This runner adds one split-display agent to the ABIDES RMSC04
reference market and measures the same paired quantity as the simulator:

    D = wealth with the display - wealth without it,

on the same seed, with the same execution schedule, marked at the true
fundamental at the close (which does not depend on trading). Each 60-second
cycle the agent alternates side, rests six equal orders one tick behind the
best quote, sends a market order for the opposite side two seconds later, and
cancels the display one second after that. The displayed total is k times the
reference depth (the median of the best bid and ask sizes at the cycle start),
so k = 6 is the six-by-ten probe, and an aggregate cap at m times reference
depth limits k to m. Arms: k = 0 (no display, the baseline), 1, 2 and 6.

Requires ABIDES (github.com/jpmorganchase/abides-jpmc-public, commit
f9cbe51342b7dedd9587e4e069040d68a5c6477f) installed with abides-core and
abides-markets, Python 3.9:

    python -m paper_study.abides_check run results/abides-check-v1 [--workers 4]
    python -m paper_study.abides_check verify results/abides-check-v1
    python -m paper_study.abides_check run results/abides-check-v2 --spec v2

In v1 the paired runs did not share a fundamental path: ABIDES generates the
fundamental lazily, so its draws depend on when agents query it, and adding the
display changes those times (only 39 of 150 display runs shared the baseline's
closing value). v2 freezes the fundamental before trading on a one-second grid,
from a fixed random state, so every arm of a seed sees the same fundamental
path, and it reports the minimum effect detectable at 80% power.
"""
import argparse
import hashlib
import json
import platform
import random
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ABIDES_COMMIT = 'f9cbe51342b7dedd9587e4e069040d68a5c6477f'
SPEC = {
    'status': 'Exploratory robustness check; seeds, arms and schedule fixed before running',
    'config': 'rmsc04', 'end_time': '10:00:00', 'seeds': list(range(1, 51)),
    'display_multiples': [0, 1, 2, 6], 'orders_per_cycle': 6, 'ticks_behind': 1,
    'execution_fraction_of_depth': 0.2, 'execution_delay_s': 2, 'cancel_delay_s': 1, 'cycle_s': 60,
    'first_cycle_after_open_s': 120,
    'bootstrap_resamples': 10000, 'bootstrap_seed': 20261117,
}
SPEC_V2 = {**SPEC,
           'status': 'Robustness check, v2: fundamental frozen before trading; seeds and arms fixed before running',
           'seeds': list(range(1, 101)), 'fundamental_grid_s': 1, 'fundamental_seed_salt': 7919,
           'bootstrap_seed': 20261122}
SPECS = {'v1': SPEC, 'v2': SPEC_V2}


def freeze_fundamental(oracle, symbol, grid_ns, seed):
    """Precompute the oracle's fundamental on a fixed grid so trading cannot change its draws."""
    import numpy as np
    state = np.random.get_state()   # megashock arrival times use the global stream
    np.random.seed(seed)
    values, t = [], oracle.mkt_open
    while t <= oracle.mkt_close:
        values.append(oracle.advance_fundamental_value_series(t, symbol))
        t += grid_ns
    np.random.set_state(state)
    start = oracle.mkt_open

    def advance(current_time, sym):
        return values[min(max(0, (current_time - start) // grid_ns), len(values) - 1)]
    oracle.advance_fundamental_value_series = advance


def make_agent_class():
    from abides_core.utils import str_to_ns
    from abides_markets.agents import TradingAgent
    from abides_markets.messages.query import QuerySpreadResponseMsg
    from abides_markets.orders import Side

    class SplitDisplayAgent(TradingAgent):
        def __init__(self, id, symbol, k, spec, random_state):
            super().__init__(id, 'SPLIT_DISPLAY', 'SplitDisplayAgent', random_state, 10_000_000)
            self.symbol, self.k, self.spec = symbol, k, spec
            self.cycle, self.phase, self.cycle_start = 0, 'idle', None
            self.depths, self.executed = [], []

        def kernel_starting(self, start_time):
            super().kernel_starting(start_time)
            self.oracle = self.kernel.oracle

        def wakeup(self, current_time):
            can_trade = super().wakeup(current_time)
            if not can_trade:
                return
            if self.cycle_start is None:   # first wakeup, at the open plus get_wake_frequency()
                self.cycle_start, self.phase = current_time, 'start'
            if self.phase == 'start':
                self.phase = 'spread'
                self.get_current_spread(self.symbol, depth=1)
            elif self.phase == 'execute':
                side = Side.ASK if self.cycle % 2 == 0 else Side.BID   # opposite of the display
                self.place_market_order(self.symbol, self.exec_qty, side)
                self.phase = 'cancel'
                self.set_wakeup(current_time + str_to_ns(f"{self.spec['cancel_delay_s']}s"))
            elif self.phase == 'cancel':
                self.cancel_all_orders()
                self.cycle += 1
                self.cycle_start += str_to_ns(f"{self.spec['cycle_s']}s")
                if self.cycle_start < self.mkt_close - str_to_ns('10s'):
                    self.phase = 'start'
                    self.set_wakeup(self.cycle_start)
                else:
                    self.phase = 'done'

        def receive_message(self, current_time, sender_id, message):
            super().receive_message(current_time, sender_id, message)
            if self.phase != 'spread' or not isinstance(message, QuerySpreadResponseMsg):
                return
            bids, asks = self.known_bids[self.symbol], self.known_asks[self.symbol]
            if not bids or not asks:
                self.phase = 'cancel'
                self.set_wakeup(current_time + 1)
                return
            depth = statistics.median([bids[0][1], asks[0][1]])
            self.depths.append(depth)
            self.exec_qty = max(1, round(self.spec['execution_fraction_of_depth'] * depth))
            total = round(self.k * depth)
            if total > 0:
                n = self.spec['orders_per_cycle']
                base, extra = divmod(total, n)
                buy = self.cycle % 2 == 0
                price = bids[0][0] - self.spec['ticks_behind'] if buy else asks[0][0] + self.spec['ticks_behind']
                for i in range(n):
                    q = base + (1 if i < extra else 0)
                    if q:
                        self.place_limit_order(self.symbol, q, Side.BID if buy else Side.ASK, price)
            self.phase = 'execute'
            self.set_wakeup(current_time + str_to_ns(f"{self.spec['execution_delay_s']}s"))

        def get_wake_frequency(self):
            return str_to_ns(f"{self.spec['first_cycle_after_open_s']}s")

        def kernel_stopping(self):
            self.fundamental_close = self.oracle.observe_price(self.symbol, self.mkt_close, self.random_state,
                                                               sigma_n=0)
            super().kernel_stopping()

        def order_executed(self, order):
            super().order_executed(order)
            self.executed.append((order.side.value if hasattr(order.side, 'value') else str(order.side),
                                  order.quantity, order.fill_price))

    return SplitDisplayAgent


def episode(seed, k, spec):
    import numpy as np
    from abides_core import abides
    from abides_markets.configs import rmsc04
    from abides_markets.utils import generate_latency_model
    cfg = rmsc04.build_config(seed=seed, end_time=spec['end_time'])
    if 'fundamental_grid_s' in spec:
        from abides_core.utils import str_to_ns
        freeze_fundamental(cfg['custom_properties']['oracle'], 'ABM', str_to_ns(f"{spec['fundamental_grid_s']}s"),
                           seed * spec['fundamental_seed_salt'] % (2**32))
    Agent = make_agent_class()
    agent = Agent(len(cfg['agents']), 'ABM', k, spec, np.random.RandomState(seed=seed))
    cfg['agents'].append(agent)
    np.random.seed(seed)   # the latency model draws from the global stream; fix it for replay
    cfg['agent_latency_model'] = generate_latency_model(len(cfg['agents']))
    end_state = abides.run(cfg)
    a = end_state['agents'][-1]
    shares = a.holdings.get('ABM', 0)
    return {'seed': seed, 'k': k, 'cash': a.holdings['CASH'], 'shares': shares,
            'fundamental_close': int(a.fundamental_close), 'cycles': a.cycle,
            'median_reference_depth': statistics.median(a.depths) if a.depths else None,
            'executed_lots': sum(q for _, q, _ in a.executed)}


def _job(args):
    return episode(*args)


def bootstrap(values, resamples, seed):
    rng = random.Random(seed)
    n = len(values)
    means = sorted(statistics.mean(values[rng.randrange(n)] for _ in range(n)) for _ in range(resamples))
    return [round(means[int(0.025 * resamples)], 6), round(means[int(0.975 * resamples) - 1], 6)]


def compute(workers, spec=SPEC):
    jobs = [(s, k, spec) for k in spec['display_multiples'] for s in spec['seeds']]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_job, jobs))
    return rows


def summarize(rows, spec=SPEC):
    base = {r['seed']: r for r in rows if r['k'] == 0}
    out = []
    for k in spec['display_multiples'][1:]:
        arm = [r for r in rows if r['k'] == k]
        # Both arms are marked at the baseline run's closing fundamental, so D reflects trading only.
        d = [(r['cash'] - base[r['seed']]['cash']) + (r['shares'] - base[r['seed']]['shares']) *
             base[r['seed']]['fundamental_close'] for r in arm]
        per_lot = [x / max(1, r['executed_lots']) for x, r in zip(d, arm)]
        out.append({'display_multiple': k, 'n': len(d), 'mean_D_cents': round(statistics.mean(d), 6),
                    'D_ci95_cents': bootstrap(d, spec['bootstrap_resamples'], f"{spec['bootstrap_seed']}|{k}"),
                    'mean_D_cents_per_executed_share': round(statistics.mean(per_lot), 6),
                    'D_per_share_ci95': bootstrap(per_lot, spec['bootstrap_resamples'],
                                                  f"{spec['bootstrap_seed']}|{k}|share"),
                    'positive_D': sum(x > 0 for x in d)})
        if spec is not SPEC:   # two-sided 5% test at 80% power: 2.8 standard errors of the paired mean
            out[-1]['mde80_cents_per_share'] = round(2.8 * statistics.stdev(per_lot) / len(per_lot) ** 0.5, 6)
    depths = [r['median_reference_depth'] for r in rows if r['median_reference_depth']]
    same_close = sum(r['fundamental_close'] == base[r['seed']]['fundamental_close'] for r in rows)
    return {'arms': out, 'median_reference_depth_shares': statistics.median(depths),
            'runs_with_baseline_closing_fundamental': f'{same_close}/{len(rows)}',
            'mean_executed_shares': round(statistics.mean(r['executed_lots'] for r in rows), 3)}


def source_hashes():
    return {str(Path(__file__).relative_to(ROOT)): hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def run(out, workers, version='v1'):
    spec = SPECS[version]
    out.mkdir(parents=True, exist_ok=False)
    rows = compute(workers, spec)
    (out / 'episodes.json').write_text(json.dumps(rows, indent=1) + '\n')
    summary = {'spec': spec, 'abides_commit': ABIDES_COMMIT, **summarize(rows, spec),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary['arms'], indent=1))


def verify(out, workers, version='v1'):
    saved = json.loads((out / 'episodes.json').read_text())
    assert json.loads(json.dumps(compute(workers, SPECS[version]))) == saved
    print(json.dumps({'episodes_reproduced': len(saved)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['run', 'verify'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--spec', choices=sorted(SPECS), default='v1')
    args = parser.parse_args()
    (run if args.command == 'run' else verify)(args.output, args.workers, args.spec)
