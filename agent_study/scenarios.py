"""Synthetic stress cases and an interface for externally supplied market tapes.

Internally authored holdouts are not independent external validation. A tape
contains one exogenous record per second and must not contain model output.
"""
import random

DEVELOPMENT = ('stationary', 'deep_book')
HELDOUT = ('liquidity_withdrawal', 'trend_reversal', 'price_jump')


def scenario(name):
    if name not in DEVELOPMENT + HELDOUT:
        raise ValueError('unknown scenario family')
    return {'id': name, 'family': name,
            'split': 'development' if name in DEVELOPMENT else 'heldout',
            'provenance': {'kind': 'internally_authored', 'independent_review': False}}


def validate_case(case, horizon):
    if not isinstance(case.get('id'), str) or not case['id']:
        raise ValueError('case needs an identifier')
    if case.get('split') not in ('development', 'heldout'):
        raise ValueError('case needs a split')
    if not isinstance(case.get('provenance'), dict):
        raise ValueError('case provenance missing')
    if 'tape' not in case:
        if case != scenario(case.get('family')):
            raise ValueError('built-in case differs from its declared family')
        return
    if case.get('family') in DEVELOPMENT + HELDOUT:
        raise ValueError('external cases need a distinct family identifier')
    if not isinstance(case.get('family'), str) or not case['family']:
        raise ValueError('external family missing')
    if case['provenance'].get('kind') != 'external_submission':
        raise ValueError('external tape needs provenance')
    if len(case['tape']) != horizon + 1:
        raise ValueError('one tape row per second is required')
    for row in case['tape']:
        if set(row) != {'fundamental', 'depth', 'noise_side', 'noise_qty'}:
            raise ValueError('invalid tape fields')
        if any(type(row[k]) is not int for k in ('fundamental', 'depth', 'noise_qty')):
            raise ValueError('tape quantities must be integers')
        if not 100 <= row['fundamental'] <= 100000 or not 1 <= row['depth'] <= 100:
            raise ValueError('invalid price or depth')
        if not 0 <= row['noise_qty'] <= 100 or row['noise_side'] not in ('buy', 'sell'):
            raise ValueError('invalid noise order')


def make_tape(case, seed, horizon):
    validate_case(case, horizon)
    if 'tape' in case:
        return [dict(row) for row in case['tape']]
    rng = random.Random(seed)
    fundamental = 1000
    rows = []
    family = case['family']
    for t in range(horizon + 1):
        if t and t % 30 == 0:
            fundamental += rng.choice([-1, 0, 0, 1])
            if family == 'trend_reversal':
                fundamental += 1 if t < horizon // 2 else -1
        if family == 'price_jump' and t == horizon // 2:
            fundamental += 8
        depth = 20 if family == 'deep_book' else 10
        if family == 'liquidity_withdrawal' and horizon // 3 <= t < 2 * horizon // 3:
            depth = 3
        rows.append({'fundamental': fundamental, 'depth': depth,
                     'noise_side': rng.choice(['buy', 'sell']),
                     'noise_qty': rng.choice([0, 0, 1, 2]) if t % 10 == 0 else 0})
    return rows
