"""Pattern alerts only. These functions never infer intent or legal violations."""
from collections import defaultdict


def detect(events, config, cancel_threshold=1.0, scale=1.0):
    submissions = {e['order_id']: e for e in events if e['kind'] == 'submit' and e.get('owner') == 'subject'}
    fills = [e for e in events if e['kind'] == 'fill']
    cancels = [e for e in events if e['kind'] == 'cancel' and e.get('owner') == 'subject']
    alerts = []
    candidates = []
    for c in cancels:
        s = submissions.get(c['order_id'])
        if not s or s['marketable']:
            continue
        filled = sum(e['qty'] for e in fills if s['order_id'] in [e['maker_id'], e['taker_id']] and e['seq'] < c['seq'])
        opposite = [e for e in fills if s['seq'] < e['seq'] < c['seq'] and
                    (e['seller'] == 'subject' if s['side'] == 'buy' else e['buyer'] == 'subject')]
        age = c['time'] - s['time']
        distance = abs(s['price'] - (s['bid'] if s['side'] == 'buy' else s['ask']))
        if age <= config['spoof_window_seconds'] * scale and filled / s['qty'] < config['spoof_max_filled_fraction'] and opposite:
            candidates.append((s, c, opposite))
            if s['qty'] >= config['spoof_size_multiple'] * scale * s['reference_depth'] and distance <= config['spoof_max_distance_ticks']:
                alerts.append({'detector': 'spoof_pattern', 'order_ids': [s['order_id']], 'time': c['time']})
    # Layers must overlap in lifetime and share an opposite-side execution.
    grouped = defaultdict(list)
    for s, c, opposite in candidates:
        for fill in opposite:
            grouped[(fill['seq'], s['side'])].append((s, c))
    for (fill_seq, side), group in grouped.items():
        prices = {s['price'] for s, c in group}
        if len(prices) >= config['layer_min_levels']:
            alerts.append({'detector': 'layer_pattern', 'order_ids': sorted({s['order_id'] for s, c in group}),
                           'time': max(c['time'] for s, c in group)})
    for e in fills:
        if e['buyer'] == e['seller'] == 'subject':
            alerts.append({'detector': 'self_match', 'order_ids': [e['maker_id'], e['taker_id']], 'time': e['time']})
    for c in cancels:
        recent_s = [s for s in submissions.values() if c['time'] - config['cancel_window_seconds'] <= s['time'] <= c['time'] and s['seq'] <= c['seq']]
        recent_c = [x for x in cancels if c['time'] - config['cancel_window_seconds'] <= x['time'] <= c['time'] and x['seq'] <= c['seq']]
        if len(recent_s) >= config['cancel_min_submissions'] and len(recent_c) / len(recent_s) > cancel_threshold:
            alerts.append({'detector': 'excessive_cancels', 'order_ids': [c['order_id']], 'time': c['time']})
            break
    return alerts


def max_cancel_ratio(events, config):
    submissions, cancels = [], []
    maximum = 0.0
    for e in events:
        if e.get('owner') != 'subject':
            continue
        if e['kind'] == 'submit':
            submissions.append(e['time'])
        elif e['kind'] == 'cancel':
            cancels.append(e['time'])
        cutoff = e['time'] - config['cancel_window_seconds']
        ns, nc = sum(t >= cutoff for t in submissions), sum(t >= cutoff for t in cancels)
        if ns >= config['cancel_min_submissions']:
            maximum = max(maximum, nc / ns)
    return maximum
