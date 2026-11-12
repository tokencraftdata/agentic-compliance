"""Pool verified LLM-agent result shards and estimate paired contrasts.

Each shard is replay-verified first. Contrasts are within-path paired
differences (everything but the contrasted factor held fixed). 95% intervals
come from a cluster bootstrap that resamples market paths (family x seed), so
repeated calls on one tape never count as independent paths. Incomplete pairs
are excluded and counted.

    python -m scripts.agent_report results/agent-llm-v1/3-heldout-haiku-* --out results/agent-llm-v1/report-haiku
"""
import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path
import random
import statistics

from agent_study.runner import verify

CONTRASTS = {
    'aggregate_vs_off': ('gate', 'off', 'aggregate'),
    'legacy_vs_off': ('gate', 'off', 'legacy'),
    'aggregate_vs_legacy': ('gate', 'legacy', 'aggregate'),
    'reminder_vs_task_only': ('prompt', 'task_only', 'conduct_reminder'),
    'response_on_vs_off': ('responsive', False, True),
}
OUTCOMES = {
    'conduct_alert': lambda r: float(r['conduct_alert']),
    'task_endpoint': lambda r: float(r['task_metrics']['completion_fraction'] if r['condition']['task'] == 'buy_parent'
                                     else r['task_metrics'].get('two_sided_filled_lots', 0)),
    'task_deviation': lambda r: float(r['task_metrics']['task_deviation']),
    'filled_lots': lambda r: float(r['market_metrics']['filled_lots']),
    'rejected_actions': lambda r: float(r['market_metrics']['rejected_actions']),
    'wealth_fundamental': lambda r: float(r['market_metrics']['wealth_fundamental']),
}
IGNORED = ('episode_id',)


def key(condition, drop):
    return json.dumps({k: v for k, v in condition.items() if k not in IGNORED + (drop,)}, sort_keys=True)


def load(shards):
    records = []
    for shard in shards:
        check = verify(shard)
        if check['mismatches']:
            raise SystemExit('replay mismatch in ' + str(shard))
        with gzip.open(shard / 'traces.jsonl.gz', 'rt') as stream:
            records.extend(json.loads(line) for line in stream)
    return records


def paired(records, factor, low, high):
    arms = defaultdict(dict)
    for r in records:
        c = r['condition']
        if c[factor] in (low, high):
            arms[key(c, factor)][c[factor]] = r
    pairs, incomplete = [], 0
    for value in arms.values():
        if set(value) != {low, high}:
            continue
        a, b = value[low], value[high]
        if a['status'] == b['status'] == 'complete':
            pairs.append((a, b))
        else:
            incomplete += 1
    return pairs, incomplete


def bootstrap(pairs, outcome, draws, rng):
    by_path = defaultdict(list)
    for a, b in pairs:
        by_path[(a['condition']['case'], a['condition']['seed'])].append(outcome(b) - outcome(a))
    paths = sorted(by_path)
    if not paths:
        return None
    flat = [d for p in paths for d in by_path[p]]
    estimates = []
    for _ in range(draws):
        sample = [d for p in (rng.choice(paths) for _ in paths) for d in by_path[p]]
        estimates.append(statistics.mean(sample))
    estimates.sort()
    return {'mean_difference': statistics.mean(flat), 'ci95': [estimates[int(0.025 * draws)], estimates[int(0.975 * draws) - 1]],
            'pairs': len(flat), 'market_paths': len(paths)}


def report(records, draws=2000, seed=20261112):
    rng = random.Random(seed)
    strata = defaultdict(list)
    for r in records:
        strata[(r['condition']['model'], r['condition']['task'])].append(r)
    rows = []
    for (model, task), group in sorted(strata.items()):
        rates = defaultdict(lambda: [0, 0, 0])
        for r in group:
            cell = rates[(r['condition']['gate'], r['condition']['prompt'])]
            cell[0] += 1
            cell[1] += r['status'] == 'complete'
            cell[2] += r['conduct_alert']
        base = {'model': model, 'task': task,
                'alert_rates': [{'gate': g, 'prompt': p, 'episodes': n, 'complete': c, 'alerts': a}
                                for (g, p), (n, c, a) in sorted(rates.items())]}
        for name, (factor, low, high) in CONTRASTS.items():
            pairs, incomplete = paired(group, factor, low, high)
            if not pairs and not incomplete:
                continue
            base[name] = {'incomplete_pairs': incomplete,
                          **{o: bootstrap(pairs, f, draws, rng) for o, f in OUTCOMES.items()}}
        rows.append(base)
    return {'episodes': len(records), 'complete': sum(r['status'] == 'complete' for r in records),
            'bootstrap': {'draws': draws, 'seed': seed, 'cluster': 'market path (case, seed)'},
            'strata': rows}


def markdown(result):
    lines = ['# LLM-agent study: paired contrasts', '',
             '%d episodes, %d complete. 95%% cluster-bootstrap intervals over market paths.' % (result['episodes'], result['complete']), '']
    for s in result['strata']:
        lines += ['## %s, %s' % (s['model'], s['task']), '',
                  '| Contrast | Alert diff [95% CI] | Task endpoint diff [95% CI] | Pairs | Paths | Incomplete |',
                  '| --- | --- | --- | --- | --- | --- |']
        for name in CONTRASTS:
            if name not in s:
                continue
            a, t = s[name]['conduct_alert'], s[name]['task_endpoint']
            if a is None:
                lines.append('| %s | n/a | n/a | 0 | 0 | %d |' % (name, s[name]['incomplete_pairs']))
                continue
            lines.append('| %s | %.3f [%.3f, %.3f] | %.3f [%.3f, %.3f] | %d | %d | %d |' % (
                name, a['mean_difference'], *a['ci95'], t['mean_difference'], *t['ci95'],
                a['pairs'], a['market_paths'], s[name]['incomplete_pairs']))
        lines.append('')
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('shards', type=Path, nargs='+')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--draws', type=int, default=2000)
    args = parser.parse_args()
    result = report(load(args.shards), args.draws)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'contrasts.json').write_text(json.dumps(result, indent=2) + '\n')
    (args.out / 'contrasts.md').write_text(markdown(result))
    print(markdown(result))
