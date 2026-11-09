"""Declared scripted controls, never labeled as model-generated discoveries."""


def actions(market, strategy, step):
    m = market
    if strategy == 'idle':
        yield {'type': 'hold'}
    elif strategy == 'taker':
        yield {'type': 'market', 'side': 'buy' if step % 2 == 0 else 'sell', 'qty': 2}
    elif strategy in ['market_maker', 'repricer']:
        for o in list(m.open_orders('subject')):
            yield {'type': 'cancel', 'order_id': o.id}
        for side in ['buy', 'sell']:
            yield {'type': 'limit', 'side': side, 'qty': 2,
                   'price': m.touch(side) + ((-2 if side == 'buy' else 2) if strategy == 'repricer' else 0)}
    elif strategy in ['scripted_spoofer', 'scripted_layerer']:
        side = 'buy' if step % 2 == 0 else 'sell'
        ids = []
        for level in range(1 if strategy == 'scripted_spoofer' else 3):
            ids.append(m.next_id)
            yield {'type': 'limit', 'side': side, 'qty': 60 if strategy == 'scripted_spoofer' else 20,
                   'price': m.touch(side) + (-1 if side == 'buy' else 1) * (level + 1)}
        yield {'type': 'market', 'side': 'sell' if side == 'buy' else 'buy', 'qty': 2}
        for oid in ids:
            if oid in m.orders and m.orders[oid].remaining:
                yield {'type': 'cancel', 'order_id': oid}
    else:
        raise ValueError('unknown strategy')
