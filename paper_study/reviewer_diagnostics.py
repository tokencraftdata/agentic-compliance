"""Read-only review diagnostics, separate from the manuscript's reported study.

Run with CPython 3.9.6:
    python3 -m paper_study.reviewer_diagnostics /tmp/reviewer-diagnostics.json

Replays the 40 saved gated responsive traces, checks their full-event hashes,
and observes the imbalance immediately before the engine's response decision.
Also tests one admissible action to show why the static balanced-book bound is
not a universal invariant. No historical source or evidence is overwritten.
"""
import argparse
import gzip
import hashlib
import json
import platform
from pathlib import Path

from pilot.revision import AggregateMarket
from pilot.run import digest

ROOT = Path(__file__).resolve().parents[1]
COHORT = ROOT / 'results/evasion-family-v0.5-window180'


class ObservedMarket(AggregateMarket):
    def background(self):
        bids = sum(o.remaining for o in self.open_orders(side='buy'))
        asks = sum(o.remaining for o in self.open_orders(side='sell'))
        if not hasattr(self, 'response_inputs'):
            self.response_inputs = []
        self.response_inputs.append({
            'time': self.time, 'bids': bids, 'asks': asks,
            'imbalance': (bids - asks) / max(1, bids + asks),
        })
        return super().background()


def audit():
    config = json.loads((COHORT / 'config.json').read_text())
    rows = []
    with gzip.open(COHORT / 'traces.jsonl.gz', 'rt') as stream:
        for line in stream:
            saved = json.loads(line)
            row = saved['metrics']
            if row['gate'] != 'aggregate' or not row['book_aware']:
                continue
            market = ObservedMarket(config, row['seed'], True, True)
            for event in saved['events']:
                if event['kind'] == 'attempt':
                    market.advance(event['time'])
                    market.act(event['action'])
            market.advance(config['session_seconds'])
            assert digest(market.events) == saved['full_event_sha256'], row['draw']
            assert len(market.events) == saved['full_event_count'], row['draw']
            rows.append({
                'draw': row['draw'], 'seed': row['seed'],
                'full_event_hash_verified': True,
                'max_abs_imbalance': max(abs(x['imbalance']) for x in market.response_inputs),
                'response_activations': sum(abs(x['imbalance']) > 0.4 for x in market.response_inputs),
            })
    assert len(rows) == 40

    market = ObservedMarket(config, 5000, True, True)
    market.act({'type': 'market', 'side': 'buy', 'qty': 30})
    rejection_reasons = [e['reason'] for e in market.events
                         if e['kind'] == 'reject' and e.get('owner') == 'subject']
    assert not rejection_reasons
    assert market.response_inputs[-1]['imbalance'] == 1.0

    negative = json.loads((ROOT / 'results/negative-controls-v0.4/summary.json').read_text())
    costs = []
    for strategy in ('large_market_maker', 'patient_large_maker'):
        cells = {c['gate']: c for c in negative['cells'] if c['strategy'] == strategy}
        off, gated = (cells[g]['filled_lots'] for g in ('off', 'aggregate'))
        costs.append({'strategy': strategy, 'ungated_lots': off, 'aggregate_lots': gated,
                      'percent_volume_loss': 100 * (off - gated) / off})
    paths = [Path(__file__), ROOT / 'pilot/market.py', ROOT / 'pilot/revision.py',
             COHORT / 'config.json', COHORT / 'traces.jsonl.gz',
             ROOT / 'results/negative-controls-v0.4/summary.json']
    return {
        'scope': 'Post-review diagnostic, not new independent validation or a sensitivity sweep',
        'python': platform.python_version(),
        'source_and_input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        'replayed_episodes': len(rows), 'full_event_hash_mismatches': 0,
        'response_activations': sum(r['response_activations'] for r in rows),
        'largest_observed_abs_imbalance': max(r['max_abs_imbalance'] for r in rows),
        'static_balanced_book': {'depth_per_level': 10, 'levels_per_side': 3,
                                'aggregate_cap': 20, 'imbalance_at_cap': 0.25,
                                'condition': 'Symmetric intact background, display on one side only; not a global invariant'},
        'allowed_market_order_counterexample': {
            'seed': 5000, 'action': {'type': 'market', 'side': 'buy', 'qty': 30},
            'rejection_reasons': rejection_reasons,
            'response_input': market.response_inputs[-1],
            'interpretation': 'Ordinary book depletion activates the response; not evidence of spoofing or profit'},
        'legitimate_maker_costs': costs, 'draws': rows,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output file; existing evidence is never overwritten.')
    result = audit()
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({k: result[k] for k in ('replayed_episodes', 'full_event_hash_mismatches',
                                            'response_activations', 'largest_observed_abs_imbalance')}, indent=2))
