"""One contract, two adapters: SQLite (dev) and DynamoDB (prod; InMemoryDynamo
implements DynamoDB condition semantics). Every test runs against both."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.app import build  # noqa: E402
from finbot.config import Settings  # noqa: E402
from finbot.faults import CrashingLLM, SimulatedCrash  # noqa: E402
from finbot.llm import FakeLLM  # noqa: E402
from finbot.runtime import Worker  # noqa: E402
from finbot.store import LeaseHeld, SessionExpired, SessionStore, StaleWrite  # noqa: E402
from finbot.store_dynamo import DynamoSessionStore, InMemoryDynamo, _CondEval  # noqa: E402

DECLINED = "txn_A1B2C3D4"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class StoreContract:
    """Mixin: subclasses provide make_store(clock)."""

    def setUp(self):
        self.clock = Clock()
        self.s = self.make_store(self.clock)
        self.addCleanup(self.s.close)
        self.s.create("s1", "m_100", {"x": 1})

    def test_create_load_tenant(self):
        self.assertEqual(self.s.load("s1"), (0, {"x": 1}))
        self.assertEqual(self.s.tenant_of("s1"), "m_100")
        with self.assertRaises(KeyError):
            self.s.load("nope")

    def test_lease_exclusive_until_expiry(self):
        self.s.claim("s1", "A", 30)
        self.s.claim("s1", "A", 30)            # renew own lease
        with self.assertRaises(LeaseHeld):
            self.s.claim("s1", "B", 30)
        self.clock.t += 31
        self.s.claim("s1", "B", 30)            # lapsed: takeover

    def test_commit_is_cas_on_version_and_lease(self):
        v = self.s.claim("s1", "A", 30)
        self.assertIsNone(self.s.begin_turn("s1", "m1", "A", "hi"))
        self.assertEqual(self.s.commit_turn("s1", "m1", "A", v, {"x": 2}, "ans", {"k": 1}), 1)
        self.assertEqual(self.s.load("s1"), (1, {"x": 2}))
        self.s.begin_turn("s1", "m2", "A", "again")
        with self.assertRaises(StaleWrite):    # stale version
            self.s.commit_turn("s1", "m2", "A", 0, {"x": 3}, "a", {})
        self.clock.t += 31
        self.s.claim("s1", "B", 30)
        with self.assertRaises(StaleWrite):    # lease lost to B
            self.s.commit_turn("s1", "m2", "A", 1, {"x": 3}, "a", {})
        self.assertEqual(self.s.load("s1"), (1, {"x": 2}))

    def test_turn_dedupe_and_incomplete(self):
        v = self.s.claim("s1", "A", 30)
        self.s.begin_turn("s1", "m1", "A", "hi")
        self.assertEqual([t["msg_id"] for t in self.s.incomplete_turns("s1")], ["m1"])
        self.s.begin_turn("s1", "m1", "B", "hi")  # retried by a replacement
        self.assertEqual(self.s.turns("s1")[0]["attempts"], 2)
        self.s.commit_turn("s1", "m1", "A", v, {}, "ans", {"blocked_by": None})
        prior = self.s.begin_turn("s1", "m1", "A", "hi")
        self.assertEqual((prior["answer"], prior["deduplicated"]), ("ans", True))
        self.assertEqual(self.s.incomplete_turns("s1"), [])

    def test_expired_session(self):
        self.clock.t += 1801
        with self.assertRaises(SessionExpired):
            self.s.load("s1")

    def test_actions_become_unknown_on_takeover_and_reconcile(self):
        self.s.claim("s1", "A", 30)
        self.assertEqual(self.s.begin_action("op1", "s1", "m1", "refund", {"amt": 5}), "new")
        self.assertEqual(self.s.begin_action("op1", "s1", "m1", "refund", {"amt": 5}), "pending")
        self.clock.t += 31
        self.s.claim("s1", "B", 30)
        self.assertEqual([a["op_id"] for a in self.s.unknown_actions("s1")], ["op1"])
        out = self.s.reconcile("s1", lambda a: {"status": "succeeded"})
        self.assertEqual(out[0]["status"], "succeeded")
        self.assertEqual(self.s.unknown_actions("s1"), [])

    def test_health_last_writer_by_time_wins(self):
        self.s.publish_health("model:p", 100.0, 30, 100.0)
        self.s.publish_health("model:p", None, 30, 90.0)   # older observation ignored
        self.assertEqual(self.s.read_health("model:p")["opened_at"], 100.0)
        self.s.publish_health("model:p", None, 30, 110.0)
        self.assertIsNone(self.s.read_health("model:p")["opened_at"])

    def test_control_config_roundtrip(self):
        self.assertIsNone(self.s.read_control("model_engine"))
        self.s.write_control("model_engine", {"drained": ["a"], "routing": "single"})
        self.assertEqual(self.s.read_control("model_engine")["drained"], ["a"])

    def test_worker_crash_and_takeover_end_to_end(self):
        bot = build(Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent", data_dir=""))
        bot.llm = CrashingLLM(FakeLLM(), crash_on_call=2)
        a = Worker(bot, self.s, "A", lease_s=20)
        sid = a.start_session("m_100", ["support"])
        with self.assertRaises(SimulatedCrash):
            a.handle(sid, "m1", f"why did {DECLINED} fail?")
        self.assertEqual(self.s.load(sid)[0], 0)
        bot_b = build(Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent", data_dir=""))
        b = Worker(bot_b, self.s, "B", lease_s=60)
        self.clock.t += 21
        r = b.handle(sid, "m1", f"why did {DECLINED} fail?")
        self.assertIn("declined", r["answer"])
        self.assertEqual(self.s.load(sid)[0], 1)
        self.assertTrue(b.handle(sid, "m1", "x")["deduplicated"])


class SqliteContract(StoreContract, unittest.TestCase):
    def make_store(self, clock):
        return SessionStore(clock=clock)


class DynamoContract(StoreContract, unittest.TestCase):
    def make_store(self, clock):
        return DynamoSessionStore(InMemoryDynamo(), clock=clock)


class ConditionEvaluator(unittest.TestCase):
    def ev(self, expr, item, values=None, names=None):
        return _CondEval(expr, names or {}, values or {}, item).run()

    def test_semantics(self):
        self.assertTrue(self.ev("attribute_not_exists(pk)", None))
        self.assertFalse(self.ev("attribute_not_exists(pk)", {"pk": "x"}))
        self.assertTrue(self.ev("#v = :v AND (attribute_not_exists(#o) OR #o = :me)", {"version": 1},
                                {":v": 1, ":me": "A"}, {"#v": "version", "#o": "owner"}))
        self.assertFalse(self.ev("#l < :now", {}, {":now": 5}, {"#l": "lease_until"}))  # missing attr -> false
        self.assertTrue(self.ev("NOT #s = :x", {"status": "a"}, {":x": "b"}, {"#s": "status"}))

    def test_rejects_unsupported(self):
        with self.assertRaises(ValueError):
            self.ev("size(#a) > :n", {}, {":n": 1}, {"#a": "a"})


class DynamoRelease(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.table = InMemoryDynamo()
        self.store = DynamoSessionStore(self.table, clock=self.clock)
        self.store.create("s1", "m_100", {"x": 0})
        self.store.claim("s1", "w")
        self.store.begin_turn("s1", "m1", "w", "first")
        self.store.commit_turn("s1", "m1", "w", 0, {"x": 1}, "first", {})

    def test_release_cannot_roll_back_a_commit_or_clear_its_lease(self):
        remove = self.table.remove_attributes

        def interleaved(pk, sk, attributes, cond):
            self.store.begin_turn("s1", "m2", "w", "second")
            self.store.commit_turn("s1", "m2", "w", 1, {"x": 2}, "second", {})
            remove(pk, sk, attributes, cond)

        self.table.remove_attributes = interleaved
        self.store.release("s1", "w")
        self.assertEqual(self.store.load("s1"), (2, {"x": 2}))
        self.assertEqual(self.table.get("S#s1", "META")["owner"], "w")
        self.assertTrue(self.store.begin_turn("s1", "m2", "w", "second")["deduplicated"])

    def test_release_cannot_clear_a_renewed_lease(self):
        remove = self.table.remove_attributes

        def interleaved(pk, sk, attributes, cond):
            self.clock.t += 1
            self.store.claim("s1", "w", 60)
            remove(pk, sk, attributes, cond)

        self.table.remove_attributes = interleaved
        self.store.release("s1", "w")
        self.assertEqual(self.table.get("S#s1", "META")["lease_until"], self.clock.t + 60)

    def test_adapter_uses_conditional_update_not_a_snapshot_put(self):
        from types import SimpleNamespace

        from finbot.store_dynamo import Boto3Dynamo, ConditionFailed

        calls = []
        client = SimpleNamespace(exceptions=SimpleNamespace(ConditionalCheckFailedException=ConditionFailed),
                                 update_item=lambda **kw: calls.append(kw))
        table = Boto3Dynamo("table", "us-east-1", client=client)
        table.remove_attributes("S#s1", "META", ["owner", "lease_until"],
                                ("#v = :v", {"#v": "version"}, {":v": 2}))
        (call,) = calls
        self.assertEqual(call["UpdateExpression"], "REMOVE #remove0, #remove1")
        self.assertEqual(call["ExpressionAttributeNames"],
                         {"#v": "version", "#remove0": "owner", "#remove1": "lease_until"})
        self.assertEqual(call["ExpressionAttributeValues"], {":v": {"N": "2"}})
        self.assertEqual(call["ConditionExpression"], "#v = :v")


class JournalRetention(unittest.TestCase):
    def test_expired_journal_cleanup_never_deletes_an_active_sessions_first_turn(self):
        clock = Clock()
        table = InMemoryDynamo()
        store = DynamoSessionStore(table, clock=clock, idle_ttl_s=30)
        store.create("s1", "m_100", {})
        for i in range(4):
            store.claim("s1", "w")
            store.begin_turn("s1", f"m{i}", "w", "hi")
            store.commit_turn("s1", f"m{i}", "w", i, {}, f"answer {i}", {})
            store.release("s1", "w")
            clock.t += 20
            # Emulate DynamoDB deleting every eligible item, including old turns.
            for key, item in list(table.items.items()):
                if item.get("expires", float("inf")) <= clock():
                    del table.items[key]
        store.claim("s1", "w")
        prior = store.begin_turn("s1", "m0", "w", "retry")
        self.assertEqual(prior["answer"], "answer 0")
        self.assertTrue(prior["deduplicated"])

    def test_active_sessions_expire_at_the_fixed_horizon_on_both_stores(self):
        from finbot.store import MAX_SESSION_LIFETIME_S

        for factory in (SessionStore, lambda **kw: DynamoSessionStore(InMemoryDynamo(), **kw)):
            clock = Clock()
            store = factory(clock=clock, idle_ttl_s=MAX_SESSION_LIFETIME_S + 100)
            self.addCleanup(store.close)
            store.create("s1", "m_100", {})
            clock.t += MAX_SESSION_LIFETIME_S - 1
            store.claim("s1", "w")
            store.begin_turn("s1", "m1", "w", "hi")
            store.commit_turn("s1", "m1", "w", 0, {}, "answer", {})
            clock.t += 1
            with self.assertRaises(SessionExpired):
                store.load("s1")
            with self.assertRaises(SessionExpired):
                store.claim("s1", "w")

    def test_legacy_dynamo_session_requires_a_fresh_session(self):
        clock = Clock()
        table = InMemoryDynamo()
        store = DynamoSessionStore(table, clock=clock)
        store.create("s1", "m_100", {})
        del table.items[("S#s1", "META")]["hard_expires"]
        with self.assertRaises(SessionExpired):
            store.begin_turn("s1", "old-message", "w", "retry")

    def test_sqlite_migrates_the_old_schema_without_losing_state(self):
        import sqlite3
        import tempfile
        from contextlib import closing

        from finbot.store import MAX_SESSION_LIFETIME_S

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.db")
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, tenant_id TEXT, version INTEGER, "
                           "owner TEXT, lease_until REAL, state TEXT, updated REAL, expires REAL)")
                db.execute("INSERT INTO sessions VALUES ('s1','m_100',1,NULL,NULL,'{}',1000000,1001800)")
                db.commit()
            store = SessionStore(path, clock=Clock())
            try:
                self.assertEqual(store.load("s1"), (1, {}))
                self.assertEqual(store.db.execute("SELECT hard_expires FROM sessions").fetchone()[0],
                                 1000000 + MAX_SESSION_LIFETIME_S)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
