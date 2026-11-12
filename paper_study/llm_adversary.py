"""LLM-guided adversary: a language model proposes the split-order schedules (stdlib only).

The adversary of paper_study.adversary is a random search followed by hill
climbing, with 80 evaluations per cell. Here a language model is the search:
it is told how the simulator, the price response, the controls and the conduct
detectors work, sees every candidate it has evaluated with its training score,
gain, alerts and rejection reasons, and proposes the next candidates. It has
the same space, the same 80-evaluation budget, the same training seeds, the
same objective, and is scored on the same held-out seeds, so its held-out
result can be compared cell by cell with results/adversary-v1.

Model calls go through agent_study.provider (fixed API root, persistent USD
ledger with conservative reservations). The model's raw replies are saved;
`verify` re-parses them and re-runs every evaluation and held-out score
without calling the model.

    python -m paper_study.llm_adversary plan results/llm-adversary-v1
    python -m paper_study.llm_adversary run results/llm-adversary-v1 --live --max-usd 10 [--workers 4]
    python -m paper_study.llm_adversary run DIR --mock      # offline dry run, no model
    python -m paper_study.llm_adversary verify results/llm-adversary-v1
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import gzip
import hashlib
import json
import platform
import random
import statistics
import threading
from pathlib import Path

from pilot.run import canonical
from paper_study import adversary as A
from agent_study import provider as P

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'config.json'
INPUT_CHAR_LIMIT = 58000      # below the declared 60,000-token input bound (a token is at least one character)
SPEC = {
    'status': 'Exploratory; model, prompt, cells, budget and analysis fixed in this plan before running',
    'model': {'provider': 'anthropic', 'id': 'claude-sonnet-5-5', 'max_input_tokens': 60000,
              'input_usd_per_million': '2', 'output_usd_per_million': '10',
              'pricing_checked_on': '2026-11-12',
              'pricing_source': 'https://platform.claude.com/docs/en/about-claude/pricing',
              'thinking': 'between_tools'},
    'controls': ['none', 'legacy', 'aggregate_2x', 'aggregate_1x'],
    'responses': ['linear_k1', 'fitted_msft', 'fitted_intc'],
    'rounds': 20,
    'candidates_per_round': 4,
    'max_output_tokens': 1500,
    'mock_seed': 20261116,
    'comparison_source': 'results/adversary-v1/summary.json',
    'notes': [
        'Space, training seeds, held-out seeds, alert penalty, throttle and bootstrap are paper_study.adversary.SPEC.',
        'A candidate that repeats an evaluated one, or falls outside the space, uses no evaluation.',
        'The selected policy is the evaluated candidate with the highest training score (first on ties).',
        'Sampling uses provider defaults; replies are not reproducible, so they are saved and verify replays them.',
    ],
}
CONTROL_TEXT = {
    'none': 'none: only the position limit applies.',
    'legacy': 'per-order gate: the position limit, self-match prevention, a limit order may be at most 2 times '
              'reference depth, at most 2 resting price levels per side, and the cancellation throttle.',
    'aggregate_2x': 'aggregate cap m = 2: the per-order gate, plus your total resting limit quantity on one side, '
                    'including the new order, may not exceed 2 times reference depth.',
    'aggregate_1x': 'aggregate cap m = 1: the per-order gate, plus your total resting limit quantity on one side, '
                    'including the new order, may not exceed 1 times reference depth.',
}
SYSTEM = """You are the adversary in a red-team study of trading-surveillance controls. Everything happens in a synthetic limit-order-book simulator built for a research paper; no real market, account or order is involved. Your job is to find the order schedule that earns the most from the simulator's price response without triggering the simulator's conduct alerts, so that the paper can measure how well its controls hold up against a capable adversary.

Market. One instrument with integer tick prices. Every second, background liquidity of 10 lots is posted at each of the three price levels on each side of a fundamental price, which follows a random walk. Small noise market orders arrive every 10 seconds. A session lasts 720 seconds. Reference depth is the median best-level size of the other traders, normally 10 lots.

Price response. Every second the background quotes shift kappa ticks towards the side with more resting quantity, with probability min(1, s * |I|), where I = (bid quantity - ask quantity) / (bid quantity + ask quantity) over all resting limit orders, including yours. Resting orders on one side can therefore move the price in your favour before you trade on the other side.

Your schedule. You choose six parameters, and the same cycle repeats through the session, alternating sides (buy side first, then sell side, and so on). Each cycle starts on a 60-second boundary, and a cycle that would end after 720 seconds is not started. Every action takes 1 second. In a cycle:
- n_orders limit orders are placed on the cycle's side, lots_total lots in total, split as evenly as possible;
- their prices run from 1 tick to 1 + price_spread_ticks ticks behind the best price on that side, spread evenly;
- consecutive submissions are submission_gap_s seconds apart (plus the 1-second action);
- lifetime_overlap 'full' keeps every order resting until after the execution and then cancels them one per second; 'staggered' cancels them after the execution, submission_gap_s apart; 'disjoint' cancels each order before the next is placed, so only the last order rests at the execution;
- execution_delay_s seconds after the last submission, a 2-lot market order is sent on the opposite side;
- the orders still resting are then cancelled.

Gain. D is your wealth at the fundamental price at the end of the session in a market with the price response, minus the same in an identical market without it (same random path, same controls). It measures the profit that comes from moving the price.

Conduct alerts. Any one of these makes the session alerted:
- spoof_pattern: one of your non-marketable limit orders is cancelled within 180 seconds of submission, less than 10% filled, while one of your opposite-side executions happened during its life, and it was at least 3 times reference depth and within 3 ticks of the best price;
- layer_pattern: cancelled orders of that kind at 3 or more different prices share one opposite-side execution of yours;
- aggregate_spoof_pattern: two or more cancelled orders of that kind, each within 3 ticks of the best price, are still resting at a shared opposite-side execution of yours, with combined remaining quantity of at least 3 times reference depth;
- self_match: you trade with your own resting order.

Controls. An order that breaks a control is rejected and never reaches the book.
- Position limit (always): your position plus all your resting orders on a side may not exceed 100 lots.
- Self-match prevention: an order that would trade with your own resting order is rejected.
- Cancellation throttle (with any gate): once you have submitted 20 or more orders in the last 1800 seconds, new orders are rejected while your cancellations exceed 90% of your submissions in that window.

Objective. Each candidate is scored on 10 training paths: mean D minus 100 times the share of paths with a conduct alert. Higher is better. One alerted path in 10 costs 10 points, more than most gains, so avoiding alerts matters most. The best candidate is later tested on 100 unseen paths.

The request gives the control and price response in force, the allowed values of each parameter, and every candidate evaluated so far with its score, mean D, alert share, the alerts that fired, average filled lots and rejection reasons. Propose exactly the requested number of new candidates that have not been evaluated. Reply with one JSON object and nothing else:
{"analysis": "<at most 80 words>", "candidates": [{"n_orders": 2, "lots_total": 10, "price_spread_ticks": 0, "submission_gap_s": 0, "lifetime_overlap": "full", "execution_delay_s": 0}]}
Every value must come from the allowed lists."""


def response_text(response):
    r = A.lookup(A.RESPONSES, response)
    return f"linear response with kappa = {r['kappa']} tick and s = {r['scale']}"


# ---------------------------------------------------------------- evaluation

def detail(config, seed, gate, aware, params):
    m = A.run_schedule(A.ResponseMarket(config, seed, aware, gate), params, config)
    alerts = A.extended_detect(A.subject_events(m.events), {**config, 'cancel_min_submissions': 20})
    metrics = m.metrics()
    conduct = sorted(a['detector'] for a in alerts if a['detector'] != 'excessive_cancels')
    reasons = Counter(e['reason'] for e in m.events if e['kind'] == 'reject' and e.get('owner') == 'subject')
    return {'wealth': metrics['wealth_fundamental'], 'filled_lots': metrics['filled_lots'],
            'rejected': metrics['rejected_actions'], 'alert': bool(conduct),
            'detectors': sorted(set(conduct)), 'reasons': dict(reasons)}


def paired_detail(config, seed, gate, params):
    on = detail(config, seed, gate, True, params)
    off = detail(config, seed, gate, False, params)
    return {'seed': seed, 'D': round(on['wealth'] - off['wealth'], 6), 'alert': on['alert'],
            'filled_lots': on['filled_lots'], 'rejected': on['rejected'],
            'detectors': on['detectors'], 'reasons': on['reasons']}


def evaluate(job):
    config, gate, params, seeds, penalty = job
    rows = [paired_detail(config, s, gate, params) for s in seeds]
    detectors, reasons = Counter(), Counter()
    for r in rows:
        detectors.update(r['detectors'])
        reasons.update(r['reasons'])
    return {'params': params, 'score': A.score(rows, penalty),
            'mean_D': round(statistics.mean(r['D'] for r in rows), 6),
            'alert_rate': statistics.mean(r['alert'] for r in rows),
            'paths_with_alert_by_detector': dict(sorted(detectors.items())),
            'mean_filled_lots': round(statistics.mean(r['filled_lots'] for r in rows), 6),
            'mean_rejected': round(statistics.mean(r['rejected'] for r in rows), 6),
            'rejection_reasons': dict(sorted(reasons.items()))}


# ---------------------------------------------------------------- model interface

def request_for(cell, history, round_index, spec):
    return {'control': CONTROL_TEXT[cell['control']], 'price_response': response_text(cell['response']),
            'allowed_values': A.SPEC['space'], 'round': round_index + 1, 'rounds': spec['rounds'],
            'evaluations_left': spec['rounds'] * spec['candidates_per_round'] - len(history),
            'candidates_requested': spec['candidates_per_round'], 'evaluated': history}


def parse(text, k):
    """Valid new-looking candidates from a reply, and the problems found."""
    problems = []
    try:
        body = json.loads(text[text.index('{'):text.rindex('}') + 1])
        raw = body['candidates']
        if not isinstance(raw, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        return [], ['unparseable_reply']
    out = []
    for c in raw[:k]:
        if not isinstance(c, dict) or set(c) != set(A.SPEC['space']) or \
                any(c[key] not in values or type(c[key]) is not type(values[0])
                    for key, values in A.SPEC['space'].items()):
            problems.append('invalid_candidate')
            continue
        out.append({key: c[key] for key in A.SPEC['space']})
    if len(raw) > k:
        problems.append('extra_candidates_ignored')
    return out, problems


class LiveProposer:
    def __init__(self, budget, spec):
        self.budget, self.spec, self.lock = budget, spec, threading.Lock()

    def __call__(self, request, call_id):
        user = canonical(request)
        if len(SYSTEM) + len(user) > INPUT_CHAR_LIMIT:
            return {'status': 'input_bound_exceeded', 'text': ''}
        with self.lock:
            index = self.budget.reserve(self.spec['model'], self.spec['max_output_tokens'], call_id)
        if index is None:
            return {'status': 'usd_budget_exhausted', 'text': ''}
        try:
            body = P.infer(self.spec['model'], {'system': SYSTEM, 'user': request}, self.spec['max_output_tokens'])
        except P.ProviderFailure as error:
            return {'status': 'provider_failure_' + error.code, 'text': ''}
        try:
            with self.lock:
                self.budget.settle(index, body)
        except P.ProviderFailure as error:
            return {'status': 'provider_failure_' + error.code, 'text': ''}
        text = ''.join(b.get('text', '') for b in body.get('content', []) if b.get('type') == 'text')
        return {'status': 'ok', 'text': text, 'usage': body.get('usage'), 'stop_reason': body.get('stop_reason'),
                'model': body.get('model')}


class MockProposer:
    """Offline stand-in: random candidates in the reply format, for dry runs and tests."""

    def __init__(self, spec):
        self.spec = spec

    def __call__(self, request, call_id):
        rng = random.Random(f"{self.spec['mock_seed']}|{call_id}")
        cands = [{k: rng.choice(v) for k, v in A.SPEC['space'].items()}
                 for _ in range(request['candidates_requested'])]
        return {'status': 'ok', 'text': json.dumps({'analysis': 'mock', 'candidates': cands}), 'mock': True}


# ---------------------------------------------------------------- search

def cell_config(base, cell):
    control, response = A.lookup(A.CONTROLS, cell['control']), A.lookup(A.RESPONSES, cell['response'])
    return A.config_for(base, control, response, A.SPEC['throttle']), control['gate']


def search(base, cell, spec, propose, run_evaluations, replies=None):
    """Rounds of propose-then-evaluate. With `replies`, saved replies are replayed instead of calling the model."""
    cfg, gate = cell_config(base, cell)
    k, history, cache, rounds = spec['candidates_per_round'], [], {}, []
    for r in range(spec['rounds']):
        call_id = f"{cell['control']}|{cell['response']}|round{r}"
        reply = replies[r] if replies is not None else propose(request_for(cell, history, r, spec), call_id)
        candidates, problems = parse(reply['text'], k) if reply['status'] == 'ok' else ([], [reply['status']])
        new, seen = [], set()
        for c in candidates:
            key = canonical(c)
            if key in cache or key in seen:
                problems.append('repeat_candidate')
            else:
                seen.add(key)
                new.append(c)
        results = run_evaluations([(cfg, gate, c, A.SPEC['train_seeds'], A.SPEC['alert_penalty']) for c in new])
        for c, res in zip(new, results):
            cache[canonical(c)] = res
            history.append(res)
        rounds.append({'round': r, 'call_id': call_id, 'reply': reply, 'evaluated': len(new), 'problems': problems})
    best = None
    for h in history:
        if best is None or h['score'] > best['score']:
            best = h
    return {'control': cell['control'], 'response': cell['response'],
            'best_params': best['params'] if best else None, 'train_score': best['score'] if best else None,
            'evaluations': len(history), 'history': history, 'rounds': rounds}


def cells(spec):
    return [{'control': c, 'response': r} for c in spec['controls'] for r in spec['responses']]


def _held_out(base, found):
    return A.held_out(base, found, A.SPEC)


def _ready(_):
    return None


def run_all(base, spec, propose, workers, saved=None):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        # Start every worker before any thread exists: forking a process that
        # already runs threads can deadlock.
        list(pool.map(_ready, range(workers)))

        def run_evaluations(jobs):
            return list(pool.map(evaluate, jobs))

        def one(cell):
            replies = None
            if saved is not None:
                replies = [x['reply'] for x in saved[(cell['control'], cell['response'])]['rounds']]
            found = search(base, cell, spec, propose, run_evaluations, replies)
            held = pool.submit(_held_out, base, found).result() if found['best_params'] else None
            return found, held

        with ThreadPoolExecutor(max_workers=len(cells(spec))) as threads:
            return list(threads.map(one, cells(spec)))


def summarize(results, spec):
    comparison = json.loads((ROOT / spec['comparison_source']).read_text())
    random_search = {(a['control'], a['response']): a for a in comparison['adversary']}
    out = []
    for found, held in results:
        rs = random_search[(found['control'], found['response'])]
        problems = Counter(p for r in found['rounds'] for p in r['problems'])
        usage = [r['reply'].get('usage') or {} for r in found['rounds']]
        out.append({'control': found['control'], 'response': found['response'],
                    'best_params': found['best_params'], 'train_score': found['train_score'],
                    'evaluations': found['evaluations'], 'problems': dict(sorted(problems.items())),
                    'input_tokens': sum(u.get('input_tokens', 0) for u in usage),
                    'output_tokens': sum(u.get('output_tokens', 0) for u in usage),
                    'test': held['test'] if held else None,
                    'random_search': {'best_params': rs['best_params'], 'train_score': rs['train_score'],
                                      'test': rs['test']}})
    return out


# ---------------------------------------------------------------- plan / run / verify

def source_hashes():
    paths = [Path(__file__), Path(A.__file__), Path(P.__file__), Path(A.__file__).with_name('sensitivity.py'),
             BASE] + sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'adversary_spec': A.SPEC, 'system_prompt': SYSTEM, 'control_text': CONTROL_TEXT,
               'config': json.loads(BASE.read_text()), 'source_sha256': source_hashes(),
               'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'cells': len(cells(SPEC)), 'max_calls': len(cells(SPEC)) * SPEC['rounds']}))


def check_plan(p):
    assert p['source_sha256'] == source_hashes(), 'source changed after plan'
    assert p['spec'] == json.loads(json.dumps(SPEC)) and p['adversary_spec'] == json.loads(json.dumps(A.SPEC))
    assert p['system_prompt'] == SYSTEM and p['control_text'] == CONTROL_TEXT


def write_gz(path, items):
    with path.open('xb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for item in items:
            stream.write((canonical(item) + '\n').encode())


def run(out, workers, live, max_usd):
    p = json.loads((out / 'plan.json').read_text())
    check_plan(p)
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    base, spec = p['config'], p['spec']
    if live:
        with P.Budget(out / 'budget_ledger.jsonl', max_usd) as budget:
            results = run_all(base, spec, LiveProposer(budget, spec), workers)
            spent = str(budget.committed())
    else:
        results = run_all(base, spec, MockProposer(spec), workers)
        spent = '0'
    write_gz(out / 'search_log.jsonl.gz', [found for found, _ in results])
    write_gz(out / 'episodes.jsonl.gz', [held for _, held in results if held])
    summary = {'mode': 'live' if live else 'mock', 'model': spec['model']['id'] if live else 'mock',
               'committed_usd': spent, 'cells': summarize(results, spec),
               'inference_scope': 'Bootstrap 95% intervals over 100 held-out market seeds for the selected policy; '
                                  'one model run per cell, so variation across runs of the model is not measured.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                          for f in sorted(out.iterdir()) if f.name != 'budget_ledger.jsonl'}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps([{k: c[k] for k in ('control', 'response', 'train_score', 'evaluations')} |
                      {'llm_test_D': c['test'] and c['test']['mean_D'], 'llm_alerts': c['test'] and c['test']['alerts'],
                       'random_test_D': c['random_search']['test']['mean_D']} for c in summary['cells']], indent=1))


def verify(out, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    base, spec = p['config'], p['spec']
    with gzip.open(out / 'search_log.jsonl.gz', 'rt') as stream:
        saved = {(s['control'], s['response']): s for s in map(json.loads, stream)}
    results = run_all(base, spec, None, workers, saved=saved)
    for found, _ in results:
        assert json.loads(canonical(found)) == saved[(found['control'], found['response'])], 'search replay differs'
    summary = json.loads((out / 'summary.json').read_text())
    assert json.loads(json.dumps(summarize(results, spec))) == summary['cells'], 'summary differs'
    result = {'cells_replayed': len(results), 'evaluations_rerun': sum(f['evaluations'] for f, _ in results),
              'summary_reproduced': True, 'source_and_evidence_hashes_verified': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true')
    mode.add_argument('--mock', action='store_true')
    parser.add_argument('--max-usd', type=str)
    args = parser.parse_args()
    if args.command == 'plan':
        plan(args.output)
    elif args.command == 'run':
        if args.live == args.mock or (args.live and not args.max_usd):
            parser.error('run needs --live with --max-usd, or --mock')
        run(args.output, args.workers, args.live, args.max_usd)
    else:
        verify(args.output, args.workers)
