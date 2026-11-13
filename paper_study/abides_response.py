"""Does an agent's own displayed depth move prices in ABIDES? (interventional check)

TradeGauntlet imposes a price response: background quotes move towards the side
with more resting depth, with a slope fitted to LOBSTER data. The fit is
predictive (organic imbalance forecasts the next price move); it does not show
that an agent's own orders move prices. paper_study.abides_check measured
the display's effect on the agent's wealth in ABIDES and found nothing, but
wealth is noisy and that check could not detect effects of the fitted size.

This check measures the mechanism directly. Every 60 seconds a probe agent in
the ABIDES RMSC04 market reads the top three levels, rests a display of c times
reference depth (six equal orders one tick behind the best quote, alternating
sides by cycle, no execution), reads the midpoint 1, 2 and 5 seconds later, and
cancels. The arm c = 0 reads the book at the same times without displaying.
The fundamental is frozen before trading (as in abides_check v2), so arms of a
seed share it. The effect of the display at horizon h is

    E_h = s * (mid_h - mid_0) in the display arm - the same in the c = 0 arm,

for the same seed and cycle, where s = +1 for a bid display and -1 for an ask
display. Dividing by the median half-spread and by the change in three-level
imbalance the display creates gives a causal slope in the units of the
LOBSTER fit. The c = 0 arm also gives the observational slope (1-second mid
change on three-level imbalance) in ABIDES itself.

Arms: no display, displays of 1, 2 and 6 times reference depth one tick behind
the best quote, and a display of 2 times reference depth five ticks behind,
which is rarely reached by trading and separates agents reacting to displayed
depth from displayed depth absorbing trades.

Two markets: RMSC04 as configured, and a thin book with 20 instead of 102 value
agents, which roughly halves the depth near the touch (in a pilot, changing the
market makers' size parameter left the depth unchanged).

Requires the ABIDES install described in paper_study.abides_check.

    python -m paper_study.abides_response plan results/abides-response-v1
    python -m paper_study.abides_response run results/abides-response-v1 [--workers 4]
    python -m paper_study.abides_response verify results/abides-response-v1
"""
import argparse
import gzip
import hashlib
import json
import platform
import random
import statistics
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from paper_study.abides_check import ABIDES_COMMIT, freeze_fundamental

ROOT = Path(__file__).resolve().parents[1]
SPEC = {
    'status': 'Exploratory robustness check; markets, arms, seeds, horizons and analysis fixed before running',
    'markets': [{'name': 'rmsc04', 'overrides': {}},
                {'name': 'thin_book', 'overrides': {'num_value_agents': 20}}],
    'end_time': '10:00:00', 'seeds': list(range(1, 101)),
    'arms': [{'name': 'none', 'c': 0, 'ticks_behind': 1}, {'name': 'c1', 'c': 1, 'ticks_behind': 1},
             {'name': 'c2', 'c': 2, 'ticks_behind': 1}, {'name': 'c6', 'c': 6, 'ticks_behind': 1},
             {'name': 'c2_far', 'c': 2, 'ticks_behind': 5}],
    'orders_per_cycle': 6,
    'probe_offsets_s': [1, 2, 5], 'cycle_s': 60, 'first_cycle_after_open_s': 120,
    'fundamental_grid_s': 1, 'fundamental_seed_salt': 7919,
    'bootstrap_resamples': 4000, 'bootstrap_seed': 20261121,
    'tradegauntlet_slopes': {'fitted_msft': 0.478911, 'fitted_intc': 0.347866, 'fitted_aapl': 0.017851},
}


def make_agent_class():
    from abides_core.utils import str_to_ns
    from abides_markets.agents import TradingAgent
    from abides_markets.messages.query import QuerySpreadResponseMsg
    from abides_markets.orders import Side

    class ProbeAgent(TradingAgent):
        def __init__(self, id, symbol, arm, spec, random_state):
            super().__init__(id, 'PROBE', 'ProbeAgent', random_state, 10_000_000)
            self.symbol, self.c, self.ticks, self.spec = symbol, arm['c'], arm['ticks_behind'], spec
            self.cycle, self.phase, self.cycle_start, self.probe = 0, 'idle', None, 0
            self.records, self.current = [], None

        def snapshot(self):
            bids, asks = self.known_bids[self.symbol], self.known_asks[self.symbol]
            if not bids or not asks:
                return None
            return {'bid': bids[0][0], 'ask': asks[0][0], 'bid_size': bids[0][1], 'ask_size': asks[0][1],
                    'bid3': sum(q for _, q in bids[:3]), 'ask3': sum(q for _, q in asks[:3])}

        def wakeup(self, current_time):
            can_trade = super().wakeup(current_time)
            if not can_trade:
                return
            if self.cycle_start is None:
                self.cycle_start, self.phase = current_time, 'pre'
            if self.phase == 'pre':
                self.phase = 'await_pre'
                self.get_current_spread(self.symbol, depth=3)
            elif self.phase == 'probe':
                self.phase = 'await_probe'
                self.get_current_spread(self.symbol, depth=3)

        def next_cycle(self):
            self.cancel_all_orders()
            if self.current is not None:
                self.records.append(self.current)
            self.current = None
            self.cycle += 1
            self.cycle_start += str_to_ns(f"{self.spec['cycle_s']}s")
            if self.cycle_start < self.mkt_close - str_to_ns('10s'):
                self.phase = 'pre'
                self.set_wakeup(self.cycle_start)
            else:
                self.phase = 'done'

        def receive_message(self, current_time, sender_id, message):
            super().receive_message(current_time, sender_id, message)
            if not isinstance(message, QuerySpreadResponseMsg):
                return
            if self.phase == 'await_pre':
                snap = self.snapshot()
                if snap is None:
                    self.current = None
                    self.next_cycle()
                    return
                buy = self.cycle % 2 == 0
                depth = statistics.median([snap['bid_size'], snap['ask_size']])
                total = round(self.c * depth)
                n = self.spec['orders_per_cycle']
                base, extra = divmod(total, n)
                price = snap['bid'] - self.ticks if buy else snap['ask'] + self.ticks
                for i in range(n):
                    q = base + (1 if i < extra else 0)
                    if q:
                        self.place_limit_order(self.symbol, q, Side.BID if buy else Side.ASK, price)
                self.current = {'cycle': self.cycle, 'side': 'bid' if buy else 'ask', 'display': total,
                                'pre': snap, 'probes': []}
                self.probe = 0
                self.phase = 'probe'
                self.set_wakeup(self.cycle_start + str_to_ns(f"{self.spec['probe_offsets_s'][0]}s"))
            elif self.phase == 'await_probe':
                snap = self.snapshot()
                self.current['probes'].append(None if snap is None else
                                              {'mid2': snap['bid'] + snap['ask'], 'bid': snap['bid'],
                                               'ask': snap['ask']})
                self.probe += 1
                if self.probe < len(self.spec['probe_offsets_s']):
                    self.phase = 'probe'
                    self.set_wakeup(self.cycle_start +
                                    str_to_ns(f"{self.spec['probe_offsets_s'][self.probe]}s"))
                else:
                    self.next_cycle()

        def order_executed(self, order):
            super().order_executed(order)
            if self.current is not None:
                self.current['filled'] = self.current.get('filled', 0) + order.quantity

        def get_wake_frequency(self):
            return str_to_ns(f"{self.spec['first_cycle_after_open_s']}s")

    return ProbeAgent


def episode(market, seed, arm, spec):
    import numpy as np
    from abides_core import abides
    from abides_core.utils import str_to_ns
    from abides_markets.configs import rmsc04
    from abides_markets.utils import generate_latency_model
    cfg = rmsc04.build_config(seed=seed, end_time=spec['end_time'], book_logging=False, log_orders=False,
                              **market['overrides'])
    freeze_fundamental(cfg['custom_properties']['oracle'], 'ABM', str_to_ns(f"{spec['fundamental_grid_s']}s"),
                       seed * spec['fundamental_seed_salt'] % (2**32))
    Agent = make_agent_class()
    agent = Agent(len(cfg['agents']), 'ABM', arm, spec, np.random.RandomState(seed=seed))
    cfg['agents'].append(agent)
    np.random.seed(seed)
    cfg['agent_latency_model'] = generate_latency_model(len(cfg['agents']))
    end_state = abides.run(cfg)
    a = end_state['agents'][-1]
    return {'market': market['name'], 'seed': seed, 'arm': arm['name'], 'cycles': a.records}


def _job(args):
    return episode(*args)


def jobs(spec):
    return [(m, s, a, spec) for m in spec['markets'] for a in spec['arms'] for s in spec['seeds']]


def compute(spec, workers):
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_job, j) for j in jobs(spec)]
        for f in as_completed(futures):
            rows.append(f.result())
    rows.sort(key=lambda r: (r['market'], r['arm'], r['seed']))
    return rows


# ---------------------------------------------------------------- analysis

def imbalance(bid3, ask3):
    return (bid3 - ask3) / (bid3 + ask3) if bid3 + ask3 else 0.0


def cluster_boot(groups, stat, resamples, seed):
    """Percentile interval of stat over resampled clusters (seeds)."""
    rng = random.Random(seed)
    keys = sorted(groups)
    draws = []
    for _ in range(resamples):
        sample = [groups[keys[rng.randrange(len(keys))]] for _ in keys]
        value = stat([x for g in sample for x in g])
        if value is not None:
            draws.append(value)
    draws.sort()
    return [round(draws[int(0.025 * len(draws))], 6), round(draws[int(0.975 * len(draws)) - 1], 6)]


def ols_slope(pairs):
    if len(pairs) < 3:
        return None
    mx = statistics.mean(x for x, _ in pairs)
    my = statistics.mean(y for _, y in pairs)
    sxx = sum((x - mx) ** 2 for x, _ in pairs)
    return sum((x - mx) * (y - my) for x, y in pairs) / sxx if sxx else None


def summarize(rows, spec):
    out = []
    for market in spec['markets']:
        by = {(r['arm'], r['seed']): {cy['cycle']: cy for cy in r['cycles']}
              for r in rows if r['market'] == market['name']}
        base_cycles = [cy for (a, s), cycles in by.items() if a == 'none' for cy in cycles.values()]
        half = statistics.median((cy['pre']['ask'] - cy['pre']['bid']) / 2 for cy in base_cycles)
        depth = statistics.median(statistics.median([cy['pre']['bid_size'], cy['pre']['ask_size']])
                                  for cy in base_cycles)
        # Observational slope in the c = 0 arm: 1-second mid change (half-spreads) on three-level imbalance.
        obs = {}
        for (a, s), cycles in by.items():
            if a != 'none':
                continue
            obs[s] = [(imbalance(cy['pre']['bid3'], cy['pre']['ask3']),
                       (cy['probes'][0]['mid2'] - (cy['pre']['bid'] + cy['pre']['ask'])) / 2 / half)
                      for cy in cycles.values() if cy['probes'] and cy['probes'][0]]
        label = f"{spec['bootstrap_seed']}|{market['name']}"
        entry = {'market': market['name'], 'median_half_spread_cents': half, 'median_reference_depth': depth,
                 'observational_slope_1s': round(ols_slope([p for g in obs.values() for p in g]), 6),
                 'observational_slope_1s_ci95': cluster_boot(obs, ols_slope, spec['bootstrap_resamples'],
                                                             label + '|obs'),
                 'arms': []}
        for arm_spec in spec['arms'][1:]:
            c = arm_spec['name']
            effects = {h: {} for h in spec['probe_offsets_s']}
            induced, fills, n = {}, 0, 0
            for s in spec['seeds']:
                disp, base = by.get((c, s), {}), by.get(('none', s), {})
                for k, cy in disp.items():
                    b = base.get(k)
                    if b is None or len(cy['probes']) != len(spec['probe_offsets_s']) or \
                            len(b['probes']) != len(spec['probe_offsets_s']):
                        continue
                    sign = 1 if cy['side'] == 'bid' else -1
                    m0d, m0b = cy['pre']['bid'] + cy['pre']['ask'], b['pre']['bid'] + b['pre']['ask']
                    for h, pd_, pb in zip(spec['probe_offsets_s'], cy['probes'], b['probes']):
                        if pd_ is None or pb is None:
                            continue
                        e = sign * ((pd_['mid2'] - m0d) - (pb['mid2'] - m0b)) / 2 / half
                        effects[h].setdefault(s, []).append(e)
                    b3, a3, d = cy['pre']['bid3'], cy['pre']['ask3'], cy['display']
                    after = imbalance(b3 + d, a3) if sign > 0 else imbalance(b3, a3 + d)
                    induced.setdefault(s, []).append(sign * (after - imbalance(b3, a3)))
                    fills += cy.get('filled', 0)
                    n += 1
            arm = {'arm': c, 'display_multiple': arm_spec['c'], 'ticks_behind': arm_spec['ticks_behind'],
                   'paired_cycles': n,
                   'mean_induced_imbalance': round(statistics.mean(x for g in induced.values() for x in g), 6),
                   'display_lots_filled_per_cycle': round(fills / n, 6) if n else None}
            for h in spec['probe_offsets_s']:
                vals = [x for g in effects[h].values() for x in g]
                se = statistics.stdev(vals) / len(vals) ** 0.5
                arm[f'effect_{h}s_half_spreads'] = round(statistics.mean(vals), 6)
                arm[f'effect_{h}s_ci95'] = cluster_boot(effects[h], statistics.mean, spec['bootstrap_resamples'],
                                                        f'{label}|{c}|{h}')
                arm[f'effect_{h}s_mde80_naive'] = round(2.8 * se, 6)
            # Causal slope at 1 s: effect per unit of induced imbalance, ratio of means over cycles.
            pairs = {s: list(zip(effects[1].get(s, []), induced.get(s, []))) for s in induced}

            def ratio(xs):
                den = statistics.mean(y for _, y in xs)
                return statistics.mean(x for x, _ in xs) / den if den else None
            arm['causal_slope_1s'] = round(ratio([p for g in pairs.values() for p in g]), 6)
            arm['causal_slope_1s_ci95'] = cluster_boot(pairs, ratio, spec['bootstrap_resamples'], f'{label}|{c}|slope')
            arm['tradegauntlet_expected_1s_effect'] = {k: round(v * arm['mean_induced_imbalance'], 6)
                                                       for k, v in spec['tradegauntlet_slopes'].items()}
            entry['arms'].append(arm)
        out.append(entry)
    return out


# ---------------------------------------------------------------- plan / run / verify

def source_hashes():
    paths = [Path(__file__), Path(__file__).with_name('abides_check.py')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'abides_commit': ABIDES_COMMIT, 'source_sha256': source_hashes(),
               'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'runs': len(jobs(SPEC))}))


def run(out, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    rows = compute(p['spec'], workers)
    with (out / 'episodes.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for r in rows:
            stream.write((json.dumps(r, sort_keys=True, separators=(',', ':')) + '\n').encode())
    summary = {'markets': summarize(rows, p['spec']), 'abides_commit': ABIDES_COMMIT,
               'inference_scope': 'Cluster-bootstrap 95% intervals over 100 seeds; cycles within a seed are not '
                                  'independent. Effects in half-spreads of mid change, display minus no display.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(summary['markets'], indent=1))


def verify(out, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    rows = compute(p['spec'], workers)
    with gzip.open(out / 'episodes.jsonl.gz', 'rt') as stream:
        assert [json.loads(line) for line in stream] == json.loads(json.dumps(rows)), 'episodes differ'
    saved = json.loads((out / 'summary.json').read_text())
    assert json.loads(json.dumps(summarize(rows, p['spec']))) == saved['markets']
    result = {'runs_reproduced': len(rows), 'summary_reproduced': True, 'source_and_evidence_hashes_verified': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    {'plan': lambda: plan(args.output), 'run': lambda: run(args.output, args.workers),
     'verify': lambda: verify(args.output, args.workers)}[args.command]()
