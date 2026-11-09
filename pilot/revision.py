"""Exploratory v0.4 controls; original v0.2 engine and detectors stay unchanged."""
from collections import defaultdict
from .market import Market
from .detectors import detect
from .strategies import actions


class AggregateMarket(Market):
    """Conservative per-owner, per-side displayed exposure cap.

    Includes all remaining same-side limit orders at every price plus the full
    submitted quantity. This may reject executable limits as well as resting
    liquidity. It is a heuristic, not a proof of absence of manipulation.
    """
    def validate(self, action):
        reason = super().validate(action)
        if reason or not self.control or action['type'] != 'limit':
            return reason
        total = sum(o.remaining for o in self.open_orders('subject', action['side']))
        if total + action['qty'] > self.config['policy_size_multiple'] * self.reference_depth():
            return 'aggregate_resting_size_limit'
        return None


def extended_detect(events, config, cancel_threshold=1.0):
    """Add a split-order signal around a common opposite-side execution.

    Only qualifying canceled orders still live at the shared fill contribute.
    Quantities are remaining just before that fill, so partial executions cannot
    inflate displayed exposure. The maximum submission-time external reference
    depth is used conservatively. Each shared fill yields at most one alert/side.
    """
    alerts = detect(events, config, cancel_threshold)
    submissions = {e['order_id']: e for e in events
                   if e['kind'] == 'submit' and e.get('owner') == 'subject'}
    fills = [e for e in events if e['kind'] == 'fill']
    groups = defaultdict(dict)
    for c in events:
        if c['kind'] != 'cancel' or c.get('owner') != 'subject':
            continue
        s = submissions.get(c['order_id'])
        if not s or s['marketable']:
            continue
        own_fills = [f for f in fills if s['order_id'] in (f['maker_id'], f['taker_id']) and f['seq'] < c['seq']]
        if c['time'] - s['time'] > config['spoof_window_seconds']:
            continue
        if sum(f['qty'] for f in own_fills) / s['qty'] >= config['spoof_max_filled_fraction']:
            continue
        if abs(s['price'] - s['bid' if s['side'] == 'buy' else 'ask']) > config['spoof_max_distance_ticks']:
            continue
        for f in fills:
            opposite = f['seller' if s['side'] == 'buy' else 'buyer'] == 'subject'
            if s['seq'] < f['seq'] < c['seq'] and opposite:
                remaining = s['qty'] - sum(x['qty'] for x in own_fills if x['seq'] < f['seq'])
                if remaining > 0:
                    groups[(f['seq'], s['side'])][s['order_id']] = (s, c, remaining)
    for (fill_seq, side), orders in sorted(groups.items()):
        if len(orders) < 2:
            continue
        quantity = sum(x[2] for x in orders.values())
        depth = max(x[0]['reference_depth'] for x in orders.values())
        if quantity >= config['spoof_size_multiple'] * depth:
            alerts.append({'detector': 'aggregate_spoof_pattern', 'order_ids': sorted(orders),
                           'fill_seq': fill_seq, 'side': side, 'displayed_qty': quantity,
                           'time': max(x[1]['time'] for x in orders.values())})
    return alerts


def review_actions(m, strategy, step):
    if strategy in ('split_3x20', 'split_6x10'):
        side = 'buy' if step % 2 == 0 else 'sell'
        price = m.touch(side) + (-1 if side == 'buy' else 1)
        count, qty = (3, 20) if strategy == 'split_3x20' else (6, 10)
        ids = []
        for _ in range(count):
            oid = m.next_id
            yield {'type': 'limit', 'side': side, 'qty': qty, 'price': price}
            if oid in m.orders and m.orders[oid].owner == 'subject':
                ids.append(oid)
        yield {'type': 'market', 'side': 'sell' if side == 'buy' else 'buy', 'qty': 2}
        for oid in ids:
            if m.orders[oid].remaining:
                yield {'type': 'cancel', 'order_id': oid}
    elif strategy == 'large_market_maker':
        for o in list(m.open_orders('subject')):
            yield {'type': 'cancel', 'order_id': o.id}
        for side in ('buy', 'sell'):
            touch = m.touch(side)
            for level in range(2):
                yield {'type': 'limit', 'side': side, 'qty': 12,
                       'price': touch + (-level if side == 'buy' else level)}
    else:
        yield from actions(m, strategy, step)


def make_market(config, seed, aware, gate):
    if gate not in ('off', 'legacy', 'aggregate'):
        raise ValueError('unknown gate')
    cls = AggregateMarket if gate == 'aggregate' else Market
    return cls(config, seed, aware, gate != 'off')


def review_episode(config, seed, strategy, aware, gate):
    m = make_market(config, seed, aware, gate)
    for step, t in enumerate(range(0, config['session_seconds'], config['decision_interval'])):
        m.advance(t)
        for a in review_actions(m, strategy, step):
            m.act(a)
    m.advance(config['session_seconds'])
    return m
