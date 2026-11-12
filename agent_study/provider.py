"""Direct Anthropic client and conservative, persistent USD reservations.

No tools, prompt caching, or fallback models. The only retries are for HTTP 429
(rate limited) and 529 (overloaded): the API rejects those requests before any
inference, so they are not billed. Any other failure retains its full
reservation because a timeout does not establish that inference was free.
"""
from decimal import Decimal
import fcntl
import json
import os
import time
from pathlib import Path
import urllib.error
import urllib.request

API_VERSION = '2026-10-21'
API_ROOT = 'https://api.anthropic.com/v1'
# The base URL is fixed: ANTHROPIC_BASE_URL is deliberately ignored because
# hosted sessions may point it at a different endpoint. The study key is read
# from TG_ANTHROPIC_API_KEY first, then ANTHROPIC_API_KEY.
KEY_VARIABLES = ('TG_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY')
UNBILLED_RETRY_CODES = (429, 529)
MAX_UNBILLED_RETRIES = 6


class ProviderFailure(RuntimeError):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


def credential():
    for name in KEY_VARIABLES:
        if os.environ.get(name):
            return os.environ[name]
    raise ProviderFailure('credential_absent')


def retry_delay(error, attempt):
    try:
        return min(60.0, max(1.0, float(error.headers.get('retry-after'))))
    except (TypeError, ValueError, AttributeError):
        return min(60.0, 2.0 ** (attempt + 1))


def api(path, payload=None, sleep=time.sleep):
    key = credential()
    request = urllib.request.Request(API_ROOT + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'x-api-key': key, 'anthropic-version': API_VERSION, 'Content-Type': 'application/json'})
    for attempt in range(MAX_UNBILLED_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code in UNBILLED_RETRY_CODES and attempt < MAX_UNBILLED_RETRIES:
                sleep(retry_delay(error, attempt))
                continue
            raise ProviderFailure('http_' + str(error.code)) from None
        except Exception as error:
            raise ProviderFailure(type(error).__name__) from None


def message_payload(model, request, max_output_tokens):
    return {'model': model['id'], 'max_tokens': max_output_tokens,
            'system': request['system'],
            'messages': [{'role': 'user', 'content': json.dumps(request['user'], sort_keys=True)}]}


def infer(model, request, max_output_tokens):
    body = api('/messages', message_payload(model, request, max_output_tokens))
    if body.get('model') != model['id']:
        raise ProviderFailure('model_identity_mismatch')
    return body


class Budget:
    """One local ledger shared across validation attempts; lock spans the run.

    Reserve an entire supported context plus maximum output at standard rates.
    This is intentionally much more conservative than estimating prompt length.
    Valid receipts release unused reservations; unknown charges stay reserved.
    """
    def __init__(self, path, cap):
        self.path = Path(path)
        self.cap = Decimal(str(cap))
        if not self.cap.is_finite() or self.cap <= 0:
            raise ValueError('positive finite budget required')
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open('a+')
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close();self.handle=None
            raise
        self.handle.seek(0)
        raw = self.handle.read()
        # Append-only snapshots: interrupted writes fail closed on the next open.
        # Never replace a paid history with a fresh zero balance after a crash.
        try:
            self.state = json.loads(raw.splitlines()[-1]) if raw else {'cap_usd': str(self.cap), 'entries': []}
        except (ValueError,IndexError):
            self.__exit__(None,None,None)
            raise ValueError('budget ledger is incomplete; manual reconciliation required') from None
        if Decimal(self.state['cap_usd']) != self.cap:
            self.__exit__(None, None, None)
            raise ValueError('existing budget cannot be changed implicitly')
        self.save()
        return self

    def save(self):
        self.handle.write(json.dumps(self.state, sort_keys=True) + '\n')
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def committed(self):
        return sum((Decimal(e['committed_usd']) for e in self.state['entries']), Decimal(0))

    def reserve(self, model, max_output_tokens, call_id):
        if any(e['call_id'] == call_id for e in self.state['entries']):
            raise ValueError('duplicate call identifier')
        price_in = Decimal(str(model['input_usd_per_million']))
        price_out = Decimal(str(model['output_usd_per_million']))
        if any(not p.is_finite() or p <= 0 for p in (price_in, price_out)):
            raise ValueError('invalid token price')
        context = model['max_input_tokens']
        if type(context) is not int or context <= 0:
            raise ValueError('model input limit missing')
        amount = (context * price_in + max_output_tokens * price_out) / Decimal(1000000)
        if self.committed() + amount > self.cap:
            return None
        self.state['entries'].append({'call_id': call_id, 'model': model['id'],
            'committed_usd': str(amount), 'reservation_usd': str(amount),
            'input_price': str(price_in), 'output_price': str(price_out),
            'status': 'reserved_or_charge_unknown'})
        self.save()
        return len(self.state['entries']) - 1

    def settle(self, index, body):
        usage = body.get('usage', {})
        if any(usage.get(k, 0) for k in ('cache_creation_input_tokens', 'cache_read_input_tokens')):
            raise ProviderFailure('unexpected_cache_billing')
        if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('input_tokens', 'output_tokens')):
            raise ProviderFailure('usage_receipt_missing')
        entry = self.state['entries'][index]
        amount = (usage['input_tokens'] * Decimal(entry['input_price']) +
                  usage['output_tokens'] * Decimal(entry['output_price'])) / Decimal(1000000)
        if amount > Decimal(entry['reservation_usd']):
            entry.update(status='reservation_exceeded', committed_usd=str(amount))
            self.save()
            raise ProviderFailure('provider_usage_exceeds_reserved_bound')
        entry.update(status='settled', committed_usd=str(amount), usage=usage)
        self.save()

    def __exit__(self, *args):
        if self.handle:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
