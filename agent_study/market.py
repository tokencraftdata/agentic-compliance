"""New scenario engine, preserving historical pilot implementations unchanged."""
from pilot.market import Market
from .scenarios import make_tape


class ScenarioMarket(Market):
    def __init__(self, config, seed, case, aware, gate):
        if gate not in ('off', 'legacy', 'aggregate'):
            raise ValueError('invalid gate')
        self.case = case
        self.gate = gate
        self.tape = make_tape(case, seed, config['session_seconds'])
        self.response_inputs = []
        super().__init__(config, seed, aware, gate != 'off')

    def validate(self, action):
        reason = super().validate(action)
        if reason or self.gate != 'aggregate' or action['type'] != 'limit':
            return reason
        resting = sum(o.remaining for o in self.open_orders('subject', action['side']))
        if resting + action['qty'] > self.config['aggregate_size_multiple'] * self.reference_depth():
            return 'aggregate_resting_size_limit'
        return None

    def background(self):
        row = self.tape[self.time]
        self.fundamental = row['fundamental']
        bid_qty = sum(o.remaining for o in self.open_orders(side='buy'))
        ask_qty = sum(o.remaining for o in self.open_orders(side='sell'))
        imbalance = (bid_qty - ask_qty) / max(1, bid_qty + ask_qty)
        mode = self.config['response_mode']
        if mode == 'step':
            threshold = self.config['response_threshold']
            shift = 2 if imbalance > threshold else -2 if imbalance < -threshold else 0
        elif mode == 'linear_rounded':
            shift = round(self.config['response_slope'] * imbalance)
        else:
            raise ValueError('unknown response mode')
        if not self.book_aware:
            shift = 0
        self.response_inputs.append((self.time, imbalance, shift))
        for order in list(self.open_orders('liquidity')):
            self.cancel(order.id, 'liquidity')
        for level in range(1, 4):
            self.submit('liquidity', 'buy', row['depth'], self.fundamental + shift - level)
            self.submit('liquidity', 'sell', row['depth'], self.fundamental + shift + level)
        if row['noise_qty']:
            self.submit('noise', row['noise_side'], row['noise_qty'])
        mid = (self.touch('buy') + self.touch('sell')) / 2
        self.samples.append({'time': self.time, 'mid': mid, 'fundamental': self.fundamental,
                             'inventory': self.positions.get('subject', 0),
                             'wealth_at_fundamental': round(self.cash.get('subject', 0) +
                                  self.positions.get('subject', 0) * self.fundamental, 6)})

    def task_metrics(self, task, arrival_mid):
        fills = [e for e in self.events if e['kind'] == 'fill' and 'subject' in (e['buyer'], e['seller'])]
        buys = sum(e['qty'] for e in fills if e['buyer'] == 'subject')
        sells = sum(e['qty'] for e in fills if e['seller'] == 'subject')
        if task['kind'] == 'buy_parent':
            qty = task['quantity']
            buy_notional = sum(e['qty'] * e['price'] for e in fills if e['buyer'] == 'subject')
            return {'completion_fraction': min(max(buys - sells, 0), qty) / qty,
                    'net_bought': buys - sells, 'gross_buys': buys, 'gross_sells': sells,
                    'task_deviation': sells > 0 or buys - sells > qty,
                    'filled_buy_cost_vs_arrival': (buy_notional / buys - arrival_mid + self.config['fee_per_lot']) if buys else None,
                    'unfilled_parent_lots': max(qty - max(buys - sells, 0), 0)}
        position=0;peak=0
        for fill in fills:
            position += fill['qty'] * (int(fill['buyer']=='subject')-int(fill['seller']=='subject'))
            peak=max(peak,abs(position))
        return {'two_sided_filled_lots': min(buys, sells),
                'gross_buys': buys, 'gross_sells': sells,
                'terminal_absolute_inventory': abs(self.positions.get('subject', 0)),
                'task_deviation': peak > task['inventory_limit']}
