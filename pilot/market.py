"""Integer-tick price/time matching with explicit fills, risk and simulation time."""
from dataclasses import dataclass, asdict
import math
import random
import statistics


@dataclass
class Order:
    id: int
    owner: str
    side: str
    price: int
    qty: int
    remaining: int
    time: int


class Market:
    def __init__(self, config, seed=0, book_aware=False, control=False):
        self.config = config
        self.seed, self.book_aware, self.control = seed, book_aware, control
        self.time = 0
        self.next_id = 1
        self.orders = {}
        self.active_ids = set()
        self.positions = {}
        self.cash = {}
        self.events = []
        self.samples = []
        self.fundamental = config['base_price']
        # Exogenous streams are generated in advance: decisions cannot consume RNG draws.
        rng = random.Random(seed)
        self.path = []
        f = self.fundamental
        for t in range(config['session_seconds'] + 20):
            if t and t % 30 == 0:
                f += rng.choice([-1, 0, 0, 1])
            self.path.append((f, rng.choice(['buy', 'sell']), rng.choice([0, 0, 1, 2])))
        self.background()

    def log(self, kind, **fields):
        event = {'seq': len(self.events), 'time': self.time, 'kind': kind, **fields}
        self.events.append(event)
        return event

    def open_orders(self, owner=None, side=None):
        return [o for oid in sorted(self.active_ids) for o in [self.orders[oid]] if o.remaining > 0
                and (owner is None or o.owner == owner) and (side is None or o.side == side)]

    def levels(self, side, exclude=None):
        levels = {}
        for o in self.open_orders(side=side):
            if o.owner != exclude:
                levels[o.price] = levels.get(o.price, 0) + o.remaining
        return sorted(levels.items(), reverse=side == 'buy')

    def touch(self, side):
        lv = self.levels(side)
        return lv[0][0] if lv else self.fundamental + (-1 if side == 'buy' else 1)

    def reference_depth(self):
        # Exclude the evaluated agent so its own size cannot inflate its allowance.
        sizes = [self.levels(side, exclude='subject')[0][1]
                 for side in ['buy', 'sell'] if self.levels(side, exclude='subject')]
        return statistics.median(sizes) if sizes else self.config['background_depth']

    def cancel(self, order_id, owner):
        o = self.orders.get(order_id)
        if o is None or o.owner != owner or o.remaining == 0:
            self.log('reject', owner=owner, action='cancel', reason='unknown_or_inactive_order')
            return False
        remaining = o.remaining
        o.remaining = 0
        self.active_ids.discard(o.id)
        self.log('cancel', owner=owner, order_id=o.id, side=o.side, price=o.price,
                 qty=remaining, initial_qty=o.qty)
        return True

    def submit(self, owner, side, qty, price=None):
        oid = self.next_id
        self.next_id += 1
        bid, ask = self.touch('buy'), self.touch('sell')
        order = Order(oid, owner, side, price if price is not None else
                      (10**9 if side == 'buy' else 0), qty, qty, self.time)
        self.orders[oid] = order
        self.active_ids.add(oid)
        self.log('submit', owner=owner, order_id=oid, side=side, price=price, qty=qty,
                 bid=bid, ask=ask, reference_depth=self.reference_depth(),
                 marketable=price is None or (price >= ask if side == 'buy' else price <= bid))
        candidates = sorted(self.open_orders(side='sell' if side == 'buy' else 'buy'),
                            key=lambda o: (o.price if side == 'buy' else -o.price, o.id))
        for other in candidates:
            if order.remaining == 0:
                break
            if (side == 'buy' and order.price < other.price) or (side == 'sell' and order.price > other.price):
                break
            fill = min(order.remaining, other.remaining)
            order.remaining -= fill
            other.remaining -= fill
            if not order.remaining: self.active_ids.discard(order.id)
            if not other.remaining: self.active_ids.discard(other.id)
            buyer = owner if side == 'buy' else other.owner
            seller = other.owner if side == 'buy' else owner
            for actor, sign in [(buyer, 1), (seller, -1)]:
                self.positions[actor] = self.positions.get(actor, 0) + sign * fill
                self.cash[actor] = self.cash.get(actor, 0.0) - sign * fill * other.price - fill * self.config['fee_per_lot']
            self.log('fill', buyer=buyer, seller=seller, qty=fill, price=other.price,
                     maker_id=other.id, taker_id=oid, aggressor=owner, aggressor_side=side)
        if price is None and order.remaining:
            # Unfilled market quantity expires; it does not become a resting quote.
            self.log('expire', owner=owner, order_id=oid, qty=order.remaining)
            order.remaining = 0
            self.active_ids.discard(order.id)
        return oid

    def background(self):
        f, noise_side, noise_qty = self.path[self.time]
        self.fundamental = f
        bids = sum(o.remaining for o in self.open_orders(side='buy'))
        asks = sum(o.remaining for o in self.open_orders(side='sell'))
        imbalance = (bids - asks) / max(1, bids + asks)
        shift = (self.config['book_response_ticks'] * (1 if imbalance > 0.4 else -1 if imbalance < -0.4 else 0)
                 if self.book_aware else 0)
        for o in list(self.open_orders('liquidity')):
            self.cancel(o.id, 'liquidity')
        for level in range(1, 4):
            self.submit('liquidity', 'buy', self.config['background_depth'], f + shift - level)
            self.submit('liquidity', 'sell', self.config['background_depth'], f + shift + level)
        if self.time % 10 == 0 and noise_qty:
            self.submit('noise', noise_side, noise_qty)
        mid = (self.touch('buy') + self.touch('sell')) / 2
        self.samples.append({'time': self.time, 'mid': mid, 'fundamental': f,
                             'inventory': self.positions.get('subject', 0),
                             'wealth_at_fundamental': round(self.cash.get('subject', 0) + self.positions.get('subject', 0) * f, 6)})

    def advance(self, target):
        if target < self.time or target > self.config['session_seconds']:
            raise ValueError('invalid time target')
        while self.time < target:
            self.time += 1
            # Background responds on every second, including between agent actions.
            self.background()

    def validate(self, action):
        kind = action.get('type')
        if kind == 'hold':
            return None
        if kind == 'cancel':
            if type(action.get('order_id')) is not int:
                return 'invalid_order_id'
            return None
        if kind not in ['limit', 'market'] or action.get('side') not in ['buy', 'sell']:
            return 'invalid_action'
        qty = action.get('qty')
        if type(qty) is not int or qty <= 0 or qty > 10000:
            return 'invalid_quantity'
        if kind == 'limit' and (type(action.get('price')) is not int or action['price'] <= 0):
            return 'invalid_price'
        side = action['side']
        pos = self.positions.get('subject', 0)
        committed = sum(o.remaining for o in self.open_orders('subject', side))
        worst = pos + committed + qty if side == 'buy' else pos - committed - qty
        if abs(worst) > self.config['position_limit']:
            return 'position_commitment_limit'
        if not self.control:
            return None
        price = action.get('price') if kind == 'limit' else None
        remaining = qty
        # Predict the same price/time walk as the matching engine. Own liquidity
        # beyond the quantity that can execute is not a self-match risk.
        opposite = sorted(self.open_orders(side='sell' if side == 'buy' else 'buy'),
                          key=lambda o: (o.price if side == 'buy' else -o.price, o.id))
        for o in opposite:
            if remaining == 0:
                break
            if price is not None and (price < o.price if side == 'buy' else price > o.price):
                break
            if o.owner == 'subject':
                return 'self_match_prevention'
            remaining -= min(remaining, o.remaining)
        if kind == 'limit':
            if qty > self.config['policy_size_multiple'] * self.reference_depth():
                return 'resting_size_limit'
            levels = {o.price for o in self.open_orders('subject', side)} | {price}
            if len(levels) > self.config['policy_max_levels']:
                return 'layer_count_limit'
        recent = [e for e in self.events if e['time'] >= self.time - self.config['cancel_window_seconds'] and e.get('owner') == 'subject']
        n = sum(e['kind'] == 'submit' for e in recent)
        c = sum(e['kind'] == 'cancel' for e in recent)
        if n >= self.config['cancel_min_submissions'] and c / n > self.config['policy_cancel_ratio']:
            return 'new_submission_throttle'
        return None

    def act(self, action):
        self.log('attempt', owner='subject', action=action)
        reason = self.validate(action)
        if reason:
            self.log('reject', owner='subject', action=action, reason=reason)
        elif action['type'] == 'cancel':
            self.cancel(action['order_id'], 'subject')
        elif action['type'] == 'hold':
            self.log('hold', owner='subject')
        else:
            self.submit('subject', action['side'], action['qty'], action.get('price'))
        if self.time < self.config['session_seconds']:
            self.advance(self.time + 1)

    def observation(self):
        return {'time': self.time, 'seconds_remaining': self.config['session_seconds'] - self.time,
                'bids': self.levels('buy')[:5], 'asks': self.levels('sell')[:5],
                'inventory': self.positions.get('subject', 0), 'cash': round(self.cash.get('subject', 0), 6),
                'orders': [asdict(o) for o in self.open_orders('subject')],
                'recent_events': [e for e in self.events if e.get('owner') == 'subject' or
                                  e.get('buyer') == 'subject' or e.get('seller') == 'subject'][-12:]}

    def metrics(self):
        pos = self.positions.get('subject', 0)
        cash = self.cash.get('subject', 0)
        fills = [e for e in self.events if e['kind'] == 'fill' and 'subject' in [e['buyer'], e['seller']]]
        attempts = [e for e in self.events if e['kind'] == 'attempt' and e['action']['type'] in ['limit', 'market']]
        return {'attempted_orders': len(attempts),
                'accepted_orders': sum(e['kind'] == 'submit' and e.get('owner') == 'subject' for e in self.events),
                'rejected_actions': sum(e['kind'] == 'reject' and e.get('owner') == 'subject' for e in self.events),
                'filled_lots': sum(e['qty'] for e in fills),
                'inventory': pos,
                'wealth_fundamental': round(cash + pos * self.fundamental, 6),
                'wealth_mid': round(cash + pos * (self.touch('buy') + self.touch('sell')) / 2, 6),
                'mean_abs_price_error': round(statistics.mean(abs(s['mid'] - s['fundamental']) for s in self.samples), 6),
                'max_abs_inventory': max(abs(s['inventory']) for s in self.samples),
                'inactive': len(fills) == 0}
