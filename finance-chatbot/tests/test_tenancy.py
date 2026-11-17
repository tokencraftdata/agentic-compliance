"""Tenant isolation below the application (finbot/tenancy.py, crypto.py, ratelimit.py).

  * session ids start with the tenant, so storage keys are tenant-first
  * the tenant is in the encryption context: a value moved to another tenant's row won't open
  * session items go through credentials for the request's tenant only (IAM LeadingKeys in infra)
  * rate limits count in the shared store, so they hold across pods
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_layers import DECLINED, Clock, container  # noqa: E402

from finbot.crypto import DecryptionError, EnvelopeCipher, LocalKeyProvider, ctx, session_tenant  # noqa: E402
from finbot.ratelimit import RateLimited, SharedRateLimiter  # noqa: E402
from finbot.security import Forbidden  # noqa: E402
from finbot.store import SessionStore  # noqa: E402
from finbot.store_dynamo import DynamoSessionStore, InMemoryDynamo  # noqa: E402
from finbot.tenancy import AssumedRoleTables, TenantScopedTable, TenantViolation, tenant_scope  # noqa: E402

A, B = "m_100.0f0e0d0c-0000-4000-8000-000000000001", "m_200.0f0e0d0c-0000-4000-8000-000000000001"


def cipher():
    return EnvelopeCipher(LocalKeyProvider({"k1": b"\x01" * 32}, "k1"))


class SessionIds(unittest.TestCase):
    def test_new_ids_name_their_tenant(self):
        c = container(Clock())
        self.addCleanup(c.close)
        sid = c.worker.start_session("m_100", ["support"], "customer")
        self.assertEqual(session_tenant(sid), "m_100")
        self.assertIsNone(session_tenant("0f0e0d0c-0000-4000-8000-000000000001"))   # an older id

    def test_store_refuses_an_id_naming_another_tenant(self):
        for store in (SessionStore(), DynamoSessionStore(InMemoryDynamo())):
            self.addCleanup(store.close)
            with self.assertRaises(ValueError):
                store.create(B, "m_100", {})


class TenantInTheEncryptionContext(unittest.TestCase):
    def test_context_names_the_tenant_and_seals_as_v2(self):
        self.assertEqual(ctx(A, "answer")["tenant_id"], "m_100")
        self.assertTrue(cipher().seal("x", ctx(A, "answer")).startswith("fbenc2:"))

    def test_a_value_moved_to_another_tenant_does_not_open(self):
        c = cipher()
        blob = c.seal("secret", {**ctx(A, "answer")})
        forged = {**ctx(A, "answer"), "tenant_id": "m_200"}   # same session id, other tenant
        with self.assertRaises(DecryptionError):
            c.open(blob, forged)

    def test_cannot_downgrade_to_the_legacy_envelope(self):
        c = cipher()
        blob = c.seal("secret", ctx(A, "answer"))
        with self.assertRaises(DecryptionError):
            c.open("fbenc1:" + blob[len("fbenc2:"):], ctx(A, "answer"))

    def test_values_sealed_before_tenant_binding_still_open(self):
        c = cipher()
        legacy = c.seal("old", {"session_id": A, "purpose": "answer"})
        self.assertTrue(legacy.startswith("fbenc1:"))
        self.assertEqual(c.open(legacy, ctx(A, "answer")), "old")


class Recorder:
    def __init__(self, name):
        self.name, self.calls = name, []

    def get(self, pk, sk):
        self.calls.append(pk)

    def put(self, item, cond=None):
        self.calls.append(item["pk"])

    def query(self, pk, p):
        self.calls.append(pk)
        return []

    def add(self, pk, sk, n, e):
        self.calls.append(pk)
        return 1

    def transact_put(self, writes):
        self.calls += [i["pk"] for i, _ in writes]


class ScopedTable(unittest.TestCase):
    def setUp(self):
        self.base, self.tenants = Recorder("base"), {}
        self.t = TenantScopedTable(self.base, lambda t: self.tenants.setdefault(t, Recorder(t)))

    def test_session_items_need_a_tenant_scope(self):
        with self.assertRaises(TenantViolation):
            self.t.get(f"S#{A}", "META")

    def test_session_items_use_the_request_tenants_credentials(self):
        with tenant_scope("m_100"):
            self.t.get(f"S#{A}", "META")
        self.assertEqual(self.tenants["m_100"].calls, [f"S#{A}"])
        self.assertEqual(self.base.calls, [])

    def test_another_tenants_session_is_refused_before_any_call(self):
        with tenant_scope("m_100"), self.assertRaises(TenantViolation):
            self.t.get(f"S#{B}", "META")
        self.assertEqual(self.tenants.get("m_100", Recorder("x")).calls, [])

    def test_shared_items_use_the_pod_role(self):
        self.t.get("H#model", "H")
        self.t.add("R#tenant:m_100:1", "R", 1, 0)
        self.assertEqual(self.base.calls, ["H#model", "R#tenant:m_100:1"])

    def test_a_transaction_cannot_span_tenants(self):
        with tenant_scope("m_100"), self.assertRaises(TenantViolation):
            self.t.transact_put([({"pk": f"S#{A}", "sk": "META"}, None), ({"pk": "H#x", "sk": "H"}, None)])


class FakeSts:
    def __init__(self):
        self.calls = []

    def assume_role(self, **kw):
        self.calls.append(kw)
        return {"Credentials": {"tenant": kw["Tags"][0]["Value"]}}


class AssumedRole(unittest.TestCase):
    def test_tag_comes_from_the_tenant_and_credentials_are_reused_then_refreshed(self):
        clock, sts = Clock(), FakeSts()
        f = AssumedRoleTables("t", "us-east-1", "arn:role", sts=sts, make_table=lambda c: c, clock=clock)
        self.assertEqual(f("m_100"), {"tenant": "m_100"})
        f("m_100")
        self.assertEqual(len(sts.calls), 1)
        self.assertEqual(sts.calls[0]["Tags"], [{"Key": "tenant_id", "Value": "m_100"}])
        clock.t += 900
        f("m_100")
        self.assertEqual(len(sts.calls), 2)


class EndToEnd(unittest.TestCase):
    """The service over a tenant-scoped DynamoDB table: owners work, other tenants get the same 403."""

    def setUp(self):
        clock = Clock()
        base = InMemoryDynamo()
        self.c = container(clock, store=DynamoSessionStore(TenantScopedTable(base, lambda t: base), clock=clock))
        self.addCleanup(self.c.close)
        v = self.c.verifier
        self.mine = v.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
        self.other = v.issue({"sub": "u2", "tenant_id": "m_200", "role": "customer"})

    def test_owner_chats_other_tenant_is_forbidden(self):
        sid = self.c.service.start(self.mine)
        r = self.c.service.send(self.mine, sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertIn("declined", r["answer"])
        with self.assertRaises(Forbidden):
            self.c.service.send(self.other, sid, "msg-0002", f"why did {DECLINED} fail?")

    def test_worker_outside_a_tenant_scope_cannot_touch_sessions(self):
        with self.assertRaises(TenantViolation):
            self.c.worker.start_session("m_100", ["support"], "customer")


class SharedLimits(unittest.TestCase):
    def check_shared(self, store, clock):
        pod1, pod2 = (SharedRateLimiter(store, 5, 1000, clock=clock) for _ in range(2))
        for pod in (pod1, pod2, pod1, pod2, pod1):
            pod.check("u1", "m_100")
        with self.assertRaises(RateLimited):
            pod2.check("u1", "m_100")                 # the sixth request, whichever pod takes it
        pod1.check("u2", "m_100")                     # another user is unaffected

    def test_limit_holds_across_pods_sqlite(self):
        clock = Clock()
        store = SessionStore(clock=clock)
        self.addCleanup(store.close)
        self.check_shared(store, clock)

    def test_limit_holds_across_pods_dynamodb(self):
        clock = Clock()
        self.check_shared(DynamoSessionStore(InMemoryDynamo(), clock=clock), clock)

    def test_window_slides(self):
        clock = Clock()
        clock.t = 1_000_040.0                          # 20 s into a minute (1_000_020 is a boundary)
        store = SessionStore(clock=clock)
        self.addCleanup(store.close)
        lim = SharedRateLimiter(store, 5, 1000, clock=clock)
        for _ in range(5):
            lim.check("u1", "m_100")
        clock.t += 60                                  # next window, a third of the way in
        for _ in range(1):                             # 5 x (1 - 1/3) = 3.3 still count: room for 1
            lim.check("u1", "m_100")
        with self.assertRaises(RateLimited):
            lim.check("u1", "m_100")
        clock.t += 120
        lim.check("u1", "m_100")                       # old windows no longer count


if __name__ == "__main__":
    unittest.main()
