"""Policy-gradient adversary over the split-order schedule space (stdlib only).

A third searcher beside the random search of paper_study.adversary and the
language model of paper_study.llm_adversary. The policy is one softmax
distribution per schedule parameter. Each iteration samples a batch of
schedules, scores each on the training seeds with the same objective (mean
paired gain D minus 100 times the alert share), and moves the logits along the
REINFORCE gradient with the batch mean as baseline and standardized
advantages. The selected schedule is the sampled one with the highest training
score, as in the other searchers, and it is scored on the same held-out seeds.

Two budgets are run. At 80 samples per cell (10 iterations of 8) the policy
gradient has the random search's budget; three independent seeds show how much
the result depends on the run. At 800 samples (100 iterations of 8) one run
shows where a longer search ends up.

    python -m paper_study.rl_adversary plan results/rl-adversary-v1
    python -m paper_study.rl_adversary run results/rl-adversary-v1 [--workers 4]
    python -m paper_study.rl_adversary verify results/rl-adversary-v1
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import hashlib
import json
import math
import platform
import random
import statistics
from pathlib import Path

from pilot.run import canonical
from paper_study import adversary as A

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'config.json'
SPEC = {
    'status': 'Exploratory; algorithm, budgets, seeds, cells and analysis fixed in this plan before running',
    'controls': ['none', 'legacy', 'aggregate_2x', 'aggregate_1x'],
    'responses': ['linear_k1', 'fitted_msft', 'fitted_intc'],
    'batch': 8,
    'learning_rate': 0.5,
    'runs': [
        {'name': 'equal_budget_seed0', 'iterations': 10, 'seed': 20261117},
        {'name': 'equal_budget_seed1', 'iterations': 10, 'seed': 20261118},
        {'name': 'equal_budget_seed2', 'iterations': 10, 'seed': 20261119},
        {'name': 'long_budget', 'iterations': 100, 'seed': 20261120},
    ],
    'comparison_source': 'results/adversary-v1/summary.json',
    'notes': [
        'Space, training and held-out seeds, alert penalty, throttle and bootstrap are paper_study.adversary.SPEC.',
        'Budget counts samples, including repeats; repeated schedules reuse their cached training score.',
        'Logits start at zero (uniform policy); advantages are standardized within each batch.',
    ],
}


def softmax(logits):
    top = max(logits)
    w = [math.exp(x - top) for x in logits]
    total = sum(w)
    return [x / total for x in w]


def sample(rng, logits):
    """Index drawn from softmax(logits) with one uniform draw."""
    u, acc = rng.random(), 0.0
    probs = softmax(logits)
    for i, p in enumerate(probs):
        acc += p
        if u < acc:
            return i
    return len(probs) - 1


def reinforce_step(logits, draws, rewards, lr):
    """In-place REINFORCE update: grad of log softmax is onehot - probs."""
    mean = statistics.mean(rewards)
    sd = statistics.pstdev(rewards)
    if sd == 0:
        return
    adv = [(r - mean) / sd for r in rewards]
    for key in logits:
        probs = softmax(logits[key])
        grad = [0.0] * len(probs)
        for d, a in zip(draws, adv):
            for i, p in enumerate(probs):
                grad[i] += a * ((1.0 if i == d[key] else 0.0) - p)
        logits[key] = [x + lr * g / len(draws) for x, g in zip(logits[key], grad)]


def objective(job):
    cfg, gate, params = job
    value, rows = A.evaluate(cfg, A.SPEC['train_seeds'], gate, params, A.SPEC['alert_penalty'])
    return {'params': params, 'score': value, 'mean_D': round(statistics.mean(r['D'] for r in rows), 6),
            'alert_rate': statistics.mean(r['alert'] for r in rows)}


def search(base, cell, run, spec, run_evaluations):
    control, response = A.lookup(A.CONTROLS, cell['control']), A.lookup(A.RESPONSES, cell['response'])
    cfg = A.config_for(base, control, response, A.SPEC['throttle'])
    space = A.SPEC['space']
    rng = random.Random(f"{run['seed']}|{cell['control']}|{cell['response']}")
    logits = {k: [0.0] * len(v) for k, v in space.items()}
    cache, trace, best = {}, [], None
    for it in range(run['iterations']):
        draws = [{k: sample(rng, logits[k]) for k in space} for _ in range(spec['batch'])]
        batch = [{k: space[k][d[k]] for k in space} for d in draws]
        todo = []
        for p in batch:
            key = canonical(p)
            if key not in cache and key not in {canonical(q) for q in todo}:
                todo.append(p)
        for p, res in zip(todo, run_evaluations([(cfg, control['gate'], p) for p in todo])):
            cache[canonical(p)] = res
        rewards = [cache[canonical(p)]['score'] for p in batch]
        for p, r in zip(batch, rewards):
            if best is None or r > best[1]:
                best = (p, r)
        reinforce_step(logits, draws, rewards, spec['learning_rate'])
        mode = {k: space[k][max(range(len(space[k])), key=lambda i: logits[k][i])] for k in space}
        trace.append({'iteration': it, 'batch_mean_score': round(statistics.mean(rewards), 6),
                      'batch_max_score': max(rewards), 'best_so_far': best[1], 'new_evaluations': len(todo),
                      'policy_mode': mode,
                      'policy_entropy': round(sum(-sum(q * math.log(q) for q in softmax(logits[k]) if q > 0)
                                                  for k in space), 6)})
    return {'control': cell['control'], 'response': cell['response'], 'run': run['name'],
            'best_params': best[0], 'train_score': best[1], 'samples': run['iterations'] * spec['batch'],
            'evaluations': len(cache), 'trace': trace, 'final_logits': logits}


def jobs(spec):
    return [({'control': c, 'response': r}, run) for run in spec['runs']
            for c in spec['controls'] for r in spec['responses']]


def _search_job(job):
    base, cell, run, spec = job
    return search(base, cell, run, spec, lambda js: [objective(j) for j in js])


def _held_out_job(job):
    base, found = job
    return A.held_out(base, found, A.SPEC)


def compute_held_out(base, searches, workers):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_held_out_job, [(base, s) for s in searches]))


def summarize(searches, held, spec):
    comparison = json.loads((ROOT / spec['comparison_source']).read_text())
    random_search = {(a['control'], a['response']): a for a in comparison['adversary']}
    out = []
    for c in spec['controls']:
        for r in spec['responses']:
            rs = random_search[(c, r)]
            runs = []
            for s, h in zip(searches, held):
                if (s['control'], s['response']) == (c, r):
                    runs.append({'run': s['run'], 'best_params': s['best_params'], 'train_score': s['train_score'],
                                 'samples': s['samples'], 'evaluations': s['evaluations'],
                                 'test': h['test']})
            out.append({'control': c, 'response': r, 'runs': runs,
                        'random_search': {'best_params': rs['best_params'], 'train_score': rs['train_score'],
                                          'test': rs['test']}})
    return out


def source_hashes():
    paths = [Path(__file__), Path(A.__file__), Path(A.__file__).with_name('sensitivity.py'), BASE] + \
        sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'adversary_spec': A.SPEC, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'searches': len(jobs(SPEC))}))


def write_gz(path, items):
    with path.open('xb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for item in items:
            stream.write((canonical(item) + '\n').encode())


def run_cmd(out, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    assert p['adversary_spec'] == json.loads(json.dumps(A.SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    base, spec = p['config'], p['spec']
    # Each finished search is checkpointed, so a restarted run skips it. The
    # searches are deterministic, so a resumed run gives the same result.
    partial = out / 'partial'
    partial.mkdir(exist_ok=True)
    name = lambda run, cell: f"{run['name']}__{cell['control']}__{cell['response']}.json"
    index = {}
    for f in partial.glob('*.json'):
        s = json.loads(f.read_text())
        index[(s['run'], s['control'], s['response'])] = s
    # Long runs first so the pool is not left with one straggler.
    order = sorted((j for j in jobs(spec) if (j[1]['name'], j[0]['control'], j[0]['response']) not in index),
                   key=lambda j: -j[1]['iterations'])
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_search_job, (base, cell, run, spec)) for cell, run in order]
        for future in as_completed(futures):
            s = future.result()
            (partial / name({'name': s['run']}, s)).write_text(canonical(s) + '\n')
            index[(s['run'], s['control'], s['response'])] = s
            print('searched', s['run'], s['control'], s['response'], s['train_score'], flush=True)
    searches = [index[(run['name'], cell['control'], cell['response'])] for cell, run in jobs(spec)]
    held = compute_held_out(base, searches, workers)
    write_gz(out / 'search_log.jsonl.gz', searches)
    for f in partial.glob('*.json'):
        f.unlink()
    partial.rmdir()
    write_gz(out / 'episodes.jsonl.gz', held)
    summary = {'cells': summarize(searches, held, spec),
               'inference_scope': 'Bootstrap 95% intervals over 100 held-out market seeds for each selected policy; '
                                  'three seeds of the policy gradient at the equal budget show run-to-run variation.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for c in summary['cells']:
        print(c['control'], c['response'], 'random', c['random_search']['test']['mean_D'],
              [(r['run'][-5:], r['test']['mean_D'], r['test']['alerts']) for r in c['runs']])


def verify(out, workers):
    """Re-scores every selected policy on the held-out seeds and checks the saved summary."""
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    with gzip.open(out / 'search_log.jsonl.gz', 'rt') as stream:
        searches = [json.loads(line) for line in stream]
    held = compute_held_out(p['config'], searches, workers)
    saved = json.loads((out / 'summary.json').read_text())
    assert json.loads(json.dumps(summarize(searches, held, p['spec']))) == saved['cells']
    result = {'held_out_policies_rerun': len(held), 'summary_reproduced': True,
              'source_and_evidence_hashes_verified': True}
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['plan', 'run', 'verify'])
    parser.add_argument('output', type=Path)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    {'plan': lambda: plan(args.output), 'run': lambda: run_cmd(args.output, args.workers),
     'verify': lambda: verify(args.output, args.workers)}[args.command]()
