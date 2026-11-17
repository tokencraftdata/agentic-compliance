"""Cache (finbot/cache.py) and AI observability (finbot/telemetry.py).

Cache: only help-page searches and breaker health are cached; records never are; a cache
failure is a miss. Telemetry: GenAI spans for turn, model call, tool call and verification,
and no user text, answer or record id ever reaches a span.
"""
import json
import unittest

from finbot.cache import CachedHealth, CachedKnowledge, MemoryCache, NullCache, RedisCache, make_cache
from finbot.config import Settings
from finbot.container import Container
from finbot.knowledge import Index
from finbot.telemetry import Telemetry, codes
from finbot.tools import DEMO_DOCS


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class MemoryCacheTests(unittest.TestCase):
    def test_ttl_and_copies(self):
        clock = Clock()
        c = MemoryCache(clock=clock)
        c.set("k", {"a": [1]}, 5)
        got = c.get("k")
        got["a"].append(2)                     # a caller's change does not leak into the cache
        self.assertEqual(c.get("k"), {"a": [1]})
        clock.t += 6
        self.assertIsNone(c.get("k"))

    def test_lru_bound(self):
        c = MemoryCache(max_entries=2)
        for k in "abc":
            c.set(k, k, 60)
        self.assertIsNone(c.get("a"))
        self.assertEqual(c.get("c"), "c")


class FailingRedis:
    def get(self, *a, **k):
        raise ConnectionError("down")

    def set(self, *a, **k):
        raise ConnectionError("down")


class RedisCacheTests(unittest.TestCase):
    def test_outage_is_a_miss_not_an_error(self):
        c = RedisCache("", client=FailingRedis())
        c.set("k", 1, 10)
        self.assertIsNone(c.get("k"))

    def test_factory(self):
        self.assertIsInstance(make_cache("none"), NullCache)
        with self.assertRaises(ValueError):
            make_cache("redis")


class CountingIndex:
    def __init__(self, kb):
        self.kb, self.calls = kb, 0

    @property
    def generation(self):
        return self.kb.generation

    def search(self, *a, **k):
        self.calls += 1
        return self.kb.search(*a, **k)


class CachedKnowledgeTests(unittest.TestCase):
    def setUp(self):
        idx = Index()
        for p in DEMO_DOCS:
            idx.upsert_page(p)
        self.idx = CountingIndex(idx)
        self.kb = CachedKnowledge(self.idx, MemoryCache(), ttl_s=60)

    def test_repeat_query_is_served_from_cache(self):
        a = self.kb.search("what does code 51 mean", groups=["support"])
        b = self.kb.search("What does   code 51 mean", groups=["support"])
        self.assertEqual(a, b)
        self.assertEqual(self.idx.calls, 1)

    def test_groups_are_part_of_the_key(self):
        self.kb.search("risk rules", groups=["support"])
        self.kb.search("risk rules", groups=["risk"])
        self.assertEqual(self.idx.calls, 2)    # restricted pages differ by group

    def test_reingest_invalidates(self):
        self.kb.search("code 51")
        self.idx.kb.upsert_page(DEMO_DOCS[0])
        self.kb.search("code 51")
        self.assertEqual(self.idx.calls, 2)


class Store:
    def __init__(self):
        self.clock, self.reads, self.h = Clock(), 0, {}

    def read_health(self, name):
        self.reads += 1
        return self.h.get(name)

    def publish_health(self, name, opened_at, cooldown, updated):
        self.h[name] = {"opened_at": opened_at, "cooldown": cooldown, "updated": updated}


class CachedHealthTests(unittest.TestCase):
    def test_reads_hit_the_store_at_most_once_per_ttl(self):
        s = Store()
        h = CachedHealth(s, ttl_s=1.0)
        for _ in range(50):
            h.read_health("m")
        self.assertEqual(s.reads, 1)
        s.clock.t += 1.1
        h.read_health("m")
        self.assertEqual(s.reads, 2)

    def test_own_publish_is_seen_at_once(self):
        s = Store()
        h = CachedHealth(s, ttl_s=60)
        self.assertIsNone(h.read_health("m"))
        h.publish_health("m", 5.0, 30, 5.0)
        self.assertEqual(h.read_health("m")["opened_at"], 5.0)


class RecordsAreNeverCached(unittest.TestCase):
    def test_record_changes_show_on_the_next_turn(self):
        c = Container(Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent",
                               data_dir="", cache="memory"))
        bot = c.chatbot
        cs = bot.new_session("m_100", ["support"], "customer")
        first = bot.ask(cs, "why did txn_A1B2C3D4 fail?")
        txn = next(t for t in c.dataset[0] if t["txn_id"] == "txn_A1B2C3D4")
        old = dict(txn)                         # shared demo data: restore every field afterwards
        try:
            txn["status"] = "approved"
            txn["decline_code"] = None
            second = bot.ask(bot.new_session("m_100", ["support"], "customer"), "what is the status of txn_A1B2C3D4?")
        finally:
            txn.clear()
            txn.update(old)
        self.assertNotEqual(first.answer, second.answer)
        self.assertIn("approved", second.answer)


class TelemetryTests(unittest.TestCase):
    def run_turn(self, text):
        s = Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent", data_dir="",
                     telemetry="memory")
        c = Container(s)
        bot = c.chatbot
        cs = bot.new_session("m_100", ["support"], "customer")
        bot.ask(cs, text)
        return c.telemetry.finished

    def test_genai_spans_for_turn_model_tool_and_verify(self):
        spans = self.run_turn("why did txn_A1B2C3D4 fail? my card is 4111 1111 1111 1111")
        names = [s["name"] for s in spans]
        self.assertIn("finbot.turn", names)
        self.assertIn("finbot.verify", names)
        self.assertTrue(any(n.startswith("chat ") for n in names))
        self.assertIn("execute_tool get_transaction", names)
        turn = next(s for s in spans if s["name"] == "finbot.turn")
        self.assertTrue(all(s["trace_id"] == turn["trace_id"] for s in spans))
        tool = next(s for s in spans if s["name"] == "execute_tool get_transaction")
        self.assertEqual(tool["attributes"]["gen_ai.operation.name"], "execute_tool")
        self.assertEqual(tool["attributes"]["finbot.tool.status"], "found")

    def test_no_user_text_answer_or_record_id_in_any_span(self):
        spans = json.dumps(self.run_turn("why did txn_A1B2C3D4 fail? my card is 4111 1111 1111 1111"))
        for secret in ("txn_A1B2C3D4", "4111", "why did"):
            self.assertNotIn(secret, spans)

    def test_unlisted_attributes_are_dropped(self):
        t = Telemetry("memory")
        with t.span("x") as sp:
            sp.set(prompt="secret text", gen_ai__request__model="m")
        self.assertEqual(t.finished[0]["attributes"], {"gen_ai.request.model": "m"})

    def test_reasons_are_cut_to_codes(self):
        self.assertEqual(codes(["unverified_url:https://evil.example", "ungrounded_amount:480.00"]),
                         ["ungrounded_amount", "unverified_url"])


if __name__ == "__main__":
    unittest.main()
