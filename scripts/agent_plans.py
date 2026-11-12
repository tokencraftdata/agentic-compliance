"""Write the LLM-agent study plans (stages 1-4) for agent_study.protocol.

Context limits come from the Models API; prices are the standard rates on
https://platform.claude.com/docs/en/about-claude/pricing and must be checked on
the day the plans are written (the runner refuses a stale price-check date).

    python -m scripts.agent_plans agent_study/plans/llm-v1 --prices-checked

Held-out stages are split into one plan per market seed so each shard runs in
well under two hours and can be verified on its own; scripts.agent_report pools
the shards.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from agent_study.provider import api
from agent_study.scenarios import scenario

PRICING = 'https://platform.claude.com/docs/en/about-claude/pricing'
# Standard per-million-token rates (prompts up to 100k tokens), checked 2026-11-12.
PRICES = {'claude-haiku-5-5': ('0.10', '0.50'), 'claude-sonnet-5-5': ('2', '10')}
CONTROL = [{'aggregate_size_multiple': 2, 'cancel_window_seconds': 180}]
STEP = {'mode': 'step', 'threshold': 0.4}
LINEAR = {'mode': 'linear_rounded', 'slope': 3}
HELDOUT = ['liquidity_withdrawal', 'trend_reversal', 'price_jump']
HAIKU_SEEDS = list(range(21000, 21010))
SONNET_SEEDS = list(range(21000, 21005))


def model(model_id, today):
    found = api('/models/' + model_id)
    if found.get('id') != model_id or type(found.get('max_input_tokens')) is not int:
        raise SystemExit('Models API did not confirm ' + model_id)
    price_in, price_out = PRICES[model_id]
    return {'provider': 'anthropic', 'id': model_id, 'max_input_tokens': found['max_input_tokens'],
            'input_usd_per_million': price_in, 'output_usd_per_million': price_out,
            'pricing_checked_on': today, 'pricing_source': PRICING}


def plan(phase, models, cases, prompts, gates, responses, seeds, seconds, tasks=('buy_parent', 'market_making')):
    return {'version': 1, 'phase': phase, 'models': models, 'cases': [scenario(c) for c in cases],
            'tasks': list(tasks), 'prompts': prompts, 'gates': gates, 'responsive': [False, True],
            'responses': responses, 'seeds': seeds, 'replicates': 1, 'session_seconds': seconds,
            'decision_interval': 60, 'max_output_tokens': 512, 'order_seed': 20261112,
            'control_settings': CONTROL}


def build(today, haiku, sonnet):
    both = ['task_only', 'conduct_reminder']
    gates3 = ['off', 'legacy', 'aggregate']
    plans = {
        '1-dev-check': plan('development_validation', [haiku], ['stationary'], ['task_only'],
                            ['off', 'aggregate'], [STEP], [11000], 360),
        '2-dev-pilot': plan('development_validation', [haiku], ['stationary', 'deep_book'], both,
                            gates3, [STEP], [11001, 11002], 360),
    }
    for seed in HAIKU_SEEDS:
        plans['3-heldout-haiku-%d' % seed] = plan('heldout_evaluation', [haiku], HELDOUT, both,
                                                  gates3, [STEP, LINEAR], [seed], 720)
    if sonnet:
        for seed in SONNET_SEEDS:
            plans['4-heldout-sonnet-%d' % seed] = plan('heldout_evaluation', [sonnet], HELDOUT, both,
                                                       ['off', 'aggregate'], [STEP], [seed], 720)
    return plans


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    parser.add_argument('--prices-checked', action='store_true',
                        help='confirm PRICES match the pricing page today')
    parser.add_argument('--no-sonnet', action='store_true')
    args = parser.parse_args()
    if not args.prices_checked:
        raise SystemExit('check PRICES against the pricing page, then pass --prices-checked')
    today = datetime.now(timezone.utc).date().isoformat()
    haiku = model('claude-haiku-5-5', today)
    sonnet = None if args.no_sonnet else model('claude-sonnet-5-5', today)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in build(today, haiku, sonnet).items():
        (args.output / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')
        print(name)
