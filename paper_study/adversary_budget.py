"""Budget check for the adaptive adversary: does a larger search change the conclusions?

paper_study.adversary uses 40 random and 40 local candidates scored on 10
training seeds. This runner repeats the searches that carry the paper's
conclusions with three times the candidates, twice the training seeds, and
twice the held-out seeds (200), reusing the same search and evaluation code.

    python -m paper_study.adversary_budget plan results/adversary-budget-v1
    python -m paper_study.adversary_budget run results/adversary-budget-v1 [--workers 4]
    python -m paper_study.adversary_budget verify results/adversary-budget-v1
"""
import argparse
import gzip
import hashlib
import json
import platform
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pilot.run import canonical
from paper_study import adversary as A
from paper_study.sensitivity import BASE, ROOT

SPEC = {**A.SPEC,
        'status': 'Budget check; search budget, seeds and cells fixed in this plan before running',
        'train_seeds': list(range(9000, 9020)),
        'test_seeds': list(range(9400, 9600)),
        'search_seed': 20261118,
        'random_candidates': 120,
        'local_steps': 120,
        'bootstrap_seed': 20261119,
        'cells': [{'control': c, 'response': r} for r in ('linear_k1', 'fitted_intc', 'fitted_msft')
                  for c in ('none', 'aggregate_2x', 'aggregate_1x')],
        'confirm_arms': []}


def _search_job(args):
    base, cell, spec = args
    found = A.search(base, A.lookup(A.CONTROLS, cell['control']), A.lookup(A.RESPONSES, cell['response']), spec)
    return found, A.held_out(base, found, spec)


def _held_out_job(args):
    return A.held_out(*args)


def source_hashes():
    paths = [Path(__file__), Path(A.__file__), Path(A.__file__).with_name('sensitivity.py'), BASE] + \
        sorted((ROOT / 'pilot').glob('*.py'))
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def plan(out):
    out.mkdir(parents=True, exist_ok=False)
    payload = {'spec': SPEC, 'config': json.loads(BASE.read_text()),
               'source_sha256': source_hashes(), 'python': platform.python_version()}
    (out / 'plan.json').write_text(json.dumps(payload, indent=2) + '\n')
    print(json.dumps({'searches': len(SPEC['cells'])}))


def run(out, workers):
    p = json.loads((out / 'plan.json').read_text())
    assert p['source_sha256'] == source_hashes() and p['spec'] == json.loads(json.dumps(SPEC))
    if (out / 'summary.json').exists():
        raise ValueError('Existing evidence cannot be overwritten')
    base, spec = p['config'], p['spec']
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_search_job, [(base, c, spec) for c in spec['cells']]))
    searches, held = [r[0] for r in results], [r[1] for r in results]
    with (out / 'search_log.jsonl.gz').open('xb') as raw, \
            gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as stream:
        for s in searches:
            stream.write((canonical(s) + '\n').encode())
    summary = {'adversary': [A.strip(h) for h in held], 'llm_episodes': 0,
               'inference_scope': 'Bootstrap 95% intervals over 200 held-out market seeds for fixed policies.'}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    manifest = {'source_sha256': source_hashes(), 'python': platform.python_version(),
                'files': {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(out.iterdir())}}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps([{k: h[k] for k in ('control', 'response')} | {'mean_D': h['test']['mean_D'],
                       'alerts': h['test']['alerts']} for h in held]))


def verify(out, workers):
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['source_sha256'] == source_hashes()
    for name, value in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == value, name
    p = json.loads((out / 'plan.json').read_text())
    with gzip.open(out / 'search_log.jsonl.gz', 'rt') as stream:
        searches = [json.loads(line) for line in stream]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        held = list(pool.map(_held_out_job, [(p['config'], s, p['spec']) for s in searches]))
    assert [A.strip(h) for h in held] == json.loads((out / 'summary.json').read_text())['adversary']
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
    {'plan': lambda: plan(args.output), 'run': lambda: run(args.output, args.workers),
     'verify': lambda: verify(args.output, args.workers)}[args.command]()
