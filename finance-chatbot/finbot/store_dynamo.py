"""DynamoDB session store: same contract as finbot/store.py, for multi-worker production.

Single-table design (table key: pk, sk; TTL attribute: expires):

    pk                sk              item
    ----------------  --------------  -----------------------------------------------
    S#<session_id>    META            tenant_id, version, owner, lease_until, state, expires
    S#<session_id>    T#<msg_id>      turn journal: status, turn_no, worker, attempts, answer, meta
    S#<session_id>    A#<op_id>       external action: kind, params, status, result
    OP#<op_id>        OP              pointer op_id -> session_id (finish_action by op id)
    H#<model>         H               shared breaker health: opened_at, cooldown, updated
    C#<key>           C               control-plane config (drained targets, routing)

Guarantees map to DynamoDB primitives:
    claim          conditional Put: owner absent | owner = me | lease_until < now
    commit_turn    TransactWriteItems: META Put conditioned on version = expected AND
                   owner = me AND lease_until >= now, plus the turn Put. Both or neither.
    begin_turn     conditional Put (attribute_not_exists) so two workers cannot both start it
    publish_health conditional Put on updated <= new (last writer by time wins)
    TTL            `expires` epoch seconds; DynamoDB deletes idle sessions in the background,
                   reads also check it because TTL deletion is lazy.

`state` is stored as a JSON string. Keep it under the 400 KB item limit: the
thread rolls over after max_turns_per_thread; for very long histories store
the history in S3 and keep a pointer here.

The store talks to a tiny DynamoTable port. Boto3Dynamo adapts it to boto3;
InMemoryDynamo implements the same port, including DynamoDB condition-expression
semantics, so the contract test runs offline against both stores.
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol

from .crypto import Codec, PlainCodec, ctx, session_tenant
from .pii import mask_pii
from .store import MAX_SESSION_LIFETIME_S, LeaseHeld, SessionExpired, StaleWrite


class ConditionFailed(Exception):
    pass


class StateTooLarge(ValueError):
    """The session would not fit in one DynamoDB item. Not retryable: the turn must shrink."""


MAX_STATE_BYTES = 350_000   # under DynamoDB's 400 KB item limit, leaving room for the other attributes


Cond = tuple[str, dict, dict] | None   # (expression, names, values)


def _strip_none(item: dict) -> dict:
    """DynamoDB stores None as a NULL attribute, which still EXISTS for
    attribute_exists(); drop it so 'not set' and 'absent' mean the same thing."""
    return {k: v for k, v in item.items() if v is not None}


class DynamoTable(Protocol):
    def get(self, pk: str, sk: str) -> dict | None: ...

    def put(self, item: dict, cond: Cond = None) -> None: ...

    def query(self, pk: str, sk_prefix: str) -> list[dict]: ...

    def remove_attributes(self, pk: str, sk: str, attributes: list[str], cond: Cond) -> None: ...

    def transact_put(self, writes: list[tuple[dict, Cond]]) -> None: ...

    def add(self, pk: str, sk: str, n: int, expires: float) -> int: ...   # atomic counter; returns the new value


# ------------------------------------------------------------------ boto3 adapter

def _to_dynamo(v):
    if isinstance(v, float):
        return Decimal(repr(v))
    if isinstance(v, dict):
        return {k: _to_dynamo(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_to_dynamo(x) for x in v]
    return v


def _from_dynamo(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, dict):
        return {k: _from_dynamo(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_dynamo(x) for x in v]
    return v


class Boto3Dynamo:
    """Adapter: DynamoTable port -> boto3. IAM role credentials (no static keys)."""

    def __init__(self, table: str, region: str, client=None):
        import boto3  # lazy
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
        from botocore.config import Config
        cfg = Config(retries={"mode": "adaptive", "max_attempts": 5}, connect_timeout=2, read_timeout=5)
        self.client = client or boto3.client("dynamodb", region_name=region, config=cfg)
        self.table, self._ser, self._de = table, TypeSerializer(), TypeDeserializer()
        self._exc = self.client.exceptions

    def _enc(self, d: dict) -> dict:
        return {k: self._ser.serialize(_to_dynamo(v)) for k, v in d.items()}

    def _dec(self, d: dict) -> dict:
        return _from_dynamo({k: self._de.deserialize(v) for k, v in d.items()})

    @staticmethod
    def _cond_kw(cond: Cond, enc) -> dict:
        if not cond:
            return {}
        expr, names, values = cond
        kw: dict[str, Any] = {"ConditionExpression": expr}
        if names:
            kw["ExpressionAttributeNames"] = names
        if values:
            kw["ExpressionAttributeValues"] = enc(values)
        return kw

    def get(self, pk, sk):
        r = self.client.get_item(TableName=self.table, Key=self._enc({"pk": pk, "sk": sk}), ConsistentRead=True)
        return self._dec(r["Item"]) if "Item" in r else None

    def put(self, item, cond=None):
        try:
            self.client.put_item(TableName=self.table, Item=self._enc(_strip_none(item)),
                                 **self._cond_kw(cond, self._enc))
        except self._exc.ConditionalCheckFailedException as e:
            raise ConditionFailed(str(e)) from None

    def remove_attributes(self, pk, sk, attributes, cond):
        kw = self._cond_kw(cond, self._enc)
        names = {f"#remove{i}": attr for i, attr in enumerate(attributes)}
        kw["ExpressionAttributeNames"] = {**kw.get("ExpressionAttributeNames", {}), **names}
        try:
            self.client.update_item(TableName=self.table, Key=self._enc({"pk": pk, "sk": sk}),
                                    UpdateExpression="REMOVE " + ", ".join(names), **kw)
        except self._exc.ConditionalCheckFailedException as e:
            raise ConditionFailed(str(e)) from None

    def query(self, pk, sk_prefix):
        out: list[dict] = []
        kw: dict[str, Any] = {}
        while True:
            r = self.client.query(TableName=self.table, ConsistentRead=True,
                                  KeyConditionExpression="pk = :pk AND begins_with(sk, :p)",
                                  ExpressionAttributeValues=self._enc({":pk": pk, ":p": sk_prefix}), **kw)
            out += [self._dec(i) for i in r.get("Items", [])]
            if "LastEvaluatedKey" not in r:
                return out
            kw = {"ExclusiveStartKey": r["LastEvaluatedKey"]}

    def add(self, pk, sk, n, expires):
        r = self.client.update_item(
            TableName=self.table, Key=self._enc({"pk": pk, "sk": sk}), ReturnValues="UPDATED_NEW",
            UpdateExpression="ADD #c :n SET #e = if_not_exists(#e, :e)",
            ExpressionAttributeNames={"#c": "count", "#e": "expires"},
            ExpressionAttributeValues=self._enc({":n": n, ":e": int(expires)}))
        return int(self._dec(r["Attributes"])["count"])

    def transact_put(self, writes):
        items = [{"Put": {"TableName": self.table, "Item": self._enc(_strip_none(i)), **self._cond_kw(c, self._enc)}}
                 for i, c in writes]
        try:
            self.client.transact_write_items(TransactItems=items)
        except self._exc.TransactionCanceledException as e:
            raise ConditionFailed(str(e)) from None


# ------------------------------------------------------------------ in-memory adapter (tests)

_TOKEN = re.compile(r"\s*(\(|\)|<=|>=|<>|=|<|>|,|AND\b|OR\b|NOT\b|attribute_not_exists|attribute_exists|"
                    r"#\w+|:\w+|\w+)")


class _CondEval:
    """Evaluates the DynamoDB condition-expression subset this store uses:
    comparisons (= <> < <= > >=), attribute_exists/attribute_not_exists, AND/OR/NOT, ()."""

    def __init__(self, expr: str, names: dict, values: dict, item: dict | None):
        self.toks = list(_TOKEN.findall(expr))
        if "".join(self.toks).replace(" ", "") != expr.replace(" ", ""):
            raise ValueError(f"unsupported condition expression: {expr}")
        self.i, self.names, self.values, self.item = 0, names, values, item or {}

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self, want=None):
        t = self.peek()
        if want and t != want:
            raise ValueError(f"expected {want}, got {t}")
        self.i += 1
        return t

    def run(self) -> bool:
        v = self.or_()
        if self.peek() is not None:
            raise ValueError(f"trailing tokens in condition: {self.toks[self.i:]}")
        return v

    def or_(self):
        v = self.and_()
        while self.peek() == "OR":
            self.take()
            rhs = self.and_()
            v = v or rhs
        return v

    def and_(self):
        v = self.not_()
        while self.peek() == "AND":
            self.take()
            rhs = self.not_()
            v = v and rhs
        return v

    def not_(self):
        if self.peek() == "NOT":
            self.take()
            return not self.not_()
        return self.atom()

    def name(self, t):
        return self.names.get(t, t) if t.startswith("#") else t

    def atom(self):
        t = self.take()
        if t == "(":
            v = self.or_()
            self.take(")")
            return v
        if t in ("attribute_exists", "attribute_not_exists"):
            self.take("(")
            a = self.name(self.take())
            self.take(")")
            present = self.item.get(a) is not None
            return present if t == "attribute_exists" else not present
        left = self.item.get(self.name(t))
        op = self.take()
        r = self.take()
        right = self.values[r] if r.startswith(":") else self.item.get(self.name(r))
        if left is None or right is None:
            return op == "<>" and left != right  # missing attribute: comparisons are false
        return {"=": left == right, "<>": left != right, "<": left < right, "<=": left <= right,
                ">": left > right, ">=": left >= right}[op]


class InMemoryDynamo:
    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def _check(self, item: dict, cond: Cond) -> None:
        if cond and not _CondEval(cond[0], cond[1] or {}, cond[2] or {},
                                  self.items.get((item["pk"], item["sk"]))).run():
            raise ConditionFailed(cond[0])

    def get(self, pk, sk):
        r = self.items.get((pk, sk))
        return json.loads(json.dumps(r)) if r else None

    def put(self, item, cond=None):
        with self._lock:
            self._check(item, cond)
            self.items[(item["pk"], item["sk"])] = json.loads(json.dumps(_strip_none(item)))

    def add(self, pk, sk, n, expires):
        with self._lock:
            item = self.items.setdefault((pk, sk), {"pk": pk, "sk": sk, "count": 0, "expires": int(expires)})
            item["count"] += n
            return item["count"]

    def remove_attributes(self, pk, sk, attributes, cond):
        with self._lock:
            self._check({"pk": pk, "sk": sk}, cond)
            item = self.items[(pk, sk)]
            for attr in attributes:
                item.pop(attr, None)

    def query(self, pk, sk_prefix):
        return [json.loads(json.dumps(v)) for (p, s), v in sorted(self.items.items())
                if p == pk and s.startswith(sk_prefix)]

    def transact_put(self, writes):
        with self._lock:
            for item, cond in writes:      # all conditions first: all-or-nothing
                self._check(item, cond)
            for item, _ in writes:
                self.items[(item["pk"], item["sk"])] = json.loads(json.dumps(_strip_none(item)))


# ------------------------------------------------------------------ the store

class DynamoSessionStore:
    def __init__(self, table: DynamoTable, clock: Callable[[], float] = time.time, idle_ttl_s: float = 1800,
                 codec: Codec | None = None):
        self.t, self.clock, self.idle_ttl = table, clock, idle_ttl_s
        self.codec: Codec = codec or PlainCodec()   # seals state, user text and answers (finbot/crypto.py)

    def close(self) -> None:
        pass

    def _meta(self, session_id: str) -> dict:
        m = self.t.get(f"S#{session_id}", "META")
        if m is None:
            raise KeyError(session_id)
        # Legacy sessions had independently expiring journal items and cannot safely dedupe.
        # Require a fresh session on upgrade rather than silently rerunning an old message.
        if min(m["expires"], m.get("hard_expires", 0)) <= self.clock():
            raise SessionExpired(session_id)
        return m

    # ---------------------------------------------------------------- sessions

    def create(self, session_id, tenant_id, state):
        if (named := session_tenant(session_id)) is not None and named != tenant_id:
            raise ValueError("session id names a different tenant")   # the id's tenant is trusted later
        now = self.clock()
        self.t.put({"pk": f"S#{session_id}", "sk": "META", "tenant_id": tenant_id, "version": 0,
                    "owner": None, "lease_until": None,
                    "state": self.codec.seal(state, ctx(session_id, "session_state")), "updated": now,
                    "expires": now + min(self.idle_ttl, MAX_SESSION_LIFETIME_S),
                    "hard_expires": now + MAX_SESSION_LIFETIME_S},
                   ("attribute_not_exists(pk)", {}, {}))

    def load(self, session_id):
        m = self._meta(session_id)
        return m["version"], self.codec.open(m["state"], ctx(session_id, "session_state"))

    def tenant_of(self, session_id):
        return self._meta(session_id)["tenant_id"]

    def claim(self, session_id, worker, lease_s=30):
        now = self.clock()
        m = self._meta(session_id)
        prev = m.get("owner")
        try:
            self.t.put({**m, "owner": worker, "lease_until": now + lease_s},
                       ("#v = :v AND (attribute_not_exists(#o) OR #o = :me OR #l < :now)",
                        {"#v": "version", "#o": "owner", "#l": "lease_until"},
                        {":v": m["version"], ":me": worker, ":now": now}))
        except ConditionFailed:
            cur = self.t.get(f"S#{session_id}", "META") or {}
            if cur.get("owner") not in (None, worker) and (cur.get("lease_until") or 0) > now:
                raise LeaseHeld(f"{session_id} held by {cur['owner']} until {cur['lease_until']:.0f}") from None
            return self.claim(session_id, worker, lease_s)  # version moved under us: re-read once more
        if prev not in (None, worker):
            # Takeover: actions the previous owner started are now of unknown outcome.
            for a in self.t.query(f"S#{session_id}", "A#"):
                if a["status"] == "pending":
                    self._put_action({**a, "status": "unknown", "updated": now})
        return m["version"]

    def release(self, session_id, worker):
        m = self._meta(session_id)
        try:
            self.t.remove_attributes(f"S#{session_id}", "META", ["owner", "lease_until"],
                                     ("#o = :me AND #v = :v AND #l = :l",
                                      {"#o": "owner", "#v": "version", "#l": "lease_until"},
                                      {":me": worker, ":v": m["version"], ":l": m.get("lease_until")}))
        except ConditionFailed:
            pass

    # ---------------------------------------------------------------- turns

    def reply(self, session_id, msg_id):
        r = self.t.get(f"S#{session_id}", f"T#{msg_id}")
        if not r or r["status"] != "committed":
            return None
        return {"answer": self.codec.open(r["answer"], ctx(session_id, "answer")),
                **(self.codec.open(r["meta"], ctx(session_id, "turn_meta")) if r.get("meta") else {})}

    def begin_turn(self, session_id, msg_id, worker, user_text):
        user_text = mask_pii(user_text)[0]   # the journal never holds card data
        now = self.clock()
        pk, sk = f"S#{session_id}", f"T#{msg_id}"
        m = self._meta(session_id)
        r = self.t.get(pk, sk)
        if r and r["status"] == "committed":
            return {"answer": self.codec.open(r["answer"], ctx(session_id, "answer")),
                    **(self.codec.open(r["meta"], ctx(session_id, "turn_meta")) if r.get("meta") else {}),
                    "deduplicated": True}
        if r:
            self.t.put({**r, "worker": worker, "attempts": r["attempts"] + 1, "started": now},
                       ("#a = :a", {"#a": "attempts"}, {":a": r["attempts"]}))
            return None
        n = len(self.t.query(pk, "T#"))
        try:
            self.t.put({"pk": pk, "sk": sk, "msg_id": msg_id, "turn_no": n + 1, "status": "started",
                        "worker": worker, "attempts": 1, "answer": None, "meta": None,
                        "user_text": self.codec.seal(user_text, ctx(session_id, "user_text")),
                        "started": now, "committed": None, "expires": m["hard_expires"]},
                       ("attribute_not_exists(pk)", {}, {}))
        except ConditionFailed:
            return self.begin_turn(session_id, msg_id, worker, user_text)  # raced: re-read
        return None

    def commit_turn(self, session_id, msg_id, worker, expected_version, state, answer, meta):
        now = self.clock()
        m = self._meta(session_id)
        if m["version"] != expected_version:
            raise StaleWrite(f"version {m['version']} != expected {expected_version}")
        if m.get("owner") != worker or not m.get("lease_until") or m["lease_until"] < now:
            raise StaleWrite(f"lease lost (owner={m.get('owner')})")
        turn = self.t.get(f"S#{session_id}", f"T#{msg_id}")
        if turn is None:
            raise StaleWrite(f"turn {msg_id} was never claimed in session {session_id}")
        blob = self.codec.seal(state, ctx(session_id, "session_state"))
        if len(blob.encode()) > MAX_STATE_BYTES:
            raise StateTooLarge(f"session state is {len(blob.encode())} bytes; the item limit is 400 KB "
                                "(lower max_history_chars)")
        new_meta = {**m, "state": blob, "version": expected_version + 1, "updated": now,
                    "expires": min(now + self.idle_ttl, m["hard_expires"])}
        # All turns outlive the session: later activity cannot extend it past this fixed horizon.
        new_turn = {**turn, "status": "committed", "answer": self.codec.seal(answer, ctx(session_id, "answer")),
                    "meta": self.codec.seal(meta, ctx(session_id, "turn_meta")), "committed": now,
                    "expires": m["hard_expires"]}
        try:
            self.t.transact_put([
                (new_meta, ("#v = :v AND #o = :me AND #l >= :now",
                            {"#v": "version", "#o": "owner", "#l": "lease_until"},
                            {":v": expected_version, ":me": worker, ":now": now})),
                (new_turn, ("#s = :started", {"#s": "status"}, {":started": "started"})),
            ])
        except ConditionFailed:
            raise StaleWrite("version or lease changed during commit") from None
        return expected_version + 1

    def incomplete_turns(self, session_id):
        return [{"msg_id": r["msg_id"], "worker": r["worker"], "attempts": r["attempts"],
                 "user_text": self.codec.open(r["user_text"], ctx(session_id, "user_text"))}
                for r in self.t.query(f"S#{session_id}", "T#") if r["status"] == "started"]

    def turns(self, session_id):
        rows = sorted(self.t.query(f"S#{session_id}", "T#"), key=lambda r: r["turn_no"])
        return [{k: r[k] for k in ("msg_id", "turn_no", "status", "worker", "attempts")} for r in rows]

    # ---------------------------------------------------------------- shared health + control

    def publish_health(self, name, opened_at, cooldown, updated):
        try:
            self.t.put({"pk": f"H#{name}", "sk": "H", "opened_at": opened_at, "cooldown": cooldown,
                        "updated": updated},
                       ("attribute_not_exists(pk) OR #u <= :u", {"#u": "updated"}, {":u": updated}))
        except ConditionFailed:
            pass  # a newer observation already won

    def read_health(self, name):
        r = self.t.get(f"H#{name}", "H")
        return {"opened_at": r.get("opened_at"), "cooldown": r["cooldown"], "updated": r["updated"]} if r else None

    def write_control(self, key, value):
        item = {"pk": f"C#{key}", "sk": "C", "value": json.dumps(value), "updated": self.clock()}
        if key.startswith("cancel:"):            # cancel flags only matter for a day; TTL removes them
            item["expires"] = int(self.clock() + 86400)
        self.t.put(item)

    def read_control(self, key):
        r = self.t.get(f"C#{key}", "C")
        return json.loads(r["value"]) if r else None

    # ---------------------------------------------------------------- shared counters (rate limits)

    def incr_counter(self, key: str, ttl_s: float) -> int:
        return self.t.add(f"R#{key}", "R", 1, self.clock() + ttl_s)

    def read_counter(self, key: str) -> int:
        r = self.t.get(f"R#{key}", "R")
        return int(r["count"]) if r and r.get("expires", 0) > self.clock() else 0

    # ---------------------------------------------------------------- side effects

    def _put_action(self, a: dict) -> None:
        self.t.put(a)

    def begin_action(self, op_id, session_id, msg_id, kind, params):
        now = self.clock()
        ptr = self.t.get(f"OP#{op_id}", "OP")
        if ptr:
            return self._action(op_id, ptr)["status"]
        try:
            self.t.transact_put([
                ({"pk": f"OP#{op_id}", "sk": "OP", "session_id": session_id}, ("attribute_not_exists(pk)", {}, {})),
                ({"pk": f"S#{session_id}", "sk": f"A#{op_id}", "op_id": op_id, "msg_id": msg_id, "kind": kind,
                  "params": json.dumps(params), "status": "pending", "result": None, "updated": now}, None),
            ])
        except ConditionFailed:
            return self.begin_action(op_id, session_id, msg_id, kind, params)
        return "new"

    def _action(self, op_id: str, ptr: dict | None = None) -> dict:
        ptr = ptr or self.t.get(f"OP#{op_id}", "OP")
        a = self.t.get(f"S#{ptr['session_id']}", f"A#{op_id}") if ptr else None
        if a is None:
            raise KeyError(f"unknown action {op_id}")
        return a

    def finish_action(self, op_id, status, result):
        a = self._action(op_id)
        self._put_action({**a, "status": status, "result": json.dumps(result), "updated": self.clock()})

    def unknown_actions(self, session_id):
        return [{"op_id": a["op_id"], "kind": a["kind"], "params": json.loads(a["params"])}
                for a in self.t.query(f"S#{session_id}", "A#") if a["status"] == "unknown"]

    def reconcile(self, session_id, check):
        out = []
        for a in self.unknown_actions(session_id):
            res = check(a)
            status = res["status"] if res else "failed"
            self.finish_action(a["op_id"], status, res or {"reason": "no record at provider"})
            out.append({**a, "status": status})
        return out


TABLE_DEFINITION = {
    # aws dynamodb create-table --cli-input-json (plus: update-time-to-live on `expires`)
    "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"},
                             {"AttributeName": "sk", "AttributeType": "S"}],
    "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
    "BillingMode": "PAY_PER_REQUEST",
    "SSESpecification": {"Enabled": True, "SSEType": "KMS"},
    "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
}
