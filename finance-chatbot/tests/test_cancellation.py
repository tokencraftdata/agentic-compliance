"""Cancelling a turn: before it starts, or while it runs (finbot/runtime.py Worker.cancel).

Cooperative: the worker checks the cancel flag at every heartbeat, i.e. before each model call.
A cancelled turn commits the pre-turn state with a fixed answer, so a resend of the same msg_id
returns "cancelled" instead of running again, and nothing the cancelled turn learned is kept.
"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from test_layers import DECLINED, Clock, container  # noqa: E402

from finbot import replies  # noqa: E402
from finbot.http_api import App  # noqa: E402
from finbot.security import Forbidden  # noqa: E402
from finbot.store_dynamo import DynamoSessionStore, InMemoryDynamo  # noqa: E402


class Counting:
    """Wraps the engine's create: counts model calls and can run a hook on the first one."""
    def __init__(self, engine, on_first=None):
        self.calls, self.on_first, self.create = 0, on_first, engine.create
        engine.create = self

    def __call__(self, *a, **k):
        self.calls += 1
        if self.calls == 1 and self.on_first:
            self.on_first()
        return self.create(*a, **k)


class Cancellation(unittest.TestCase):
    def make(self, store=None):
        self.clock = Clock()
        c = container(self.clock, store=store)
        self.addCleanup(c.close)
        return c

    def test_cancelled_before_it_starts_makes_no_model_call(self):
        c = self.make()
        sid = c.worker.start_session("m_100", ["support"], "customer")
        calls = Counting(c.chatbot.engine())
        c.worker.cancel(sid, "msg-0001", by="u1")
        r = c.worker.handle(sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertEqual(r["blocked_by"], "cancelled")
        self.assertEqual(r["answer"], replies.CANCELLED)
        self.assertEqual(calls.calls, 0)

    def test_resend_after_cancel_is_deduplicated(self):
        c = self.make()
        sid = c.worker.start_session("m_100", ["support"], "customer")
        c.worker.cancel(sid, "msg-0001")
        c.worker.handle(sid, "msg-0001", f"why did {DECLINED} fail?")
        again = c.worker.handle(sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertTrue(again["deduplicated"])
        self.assertEqual(again["answer"], replies.CANCELLED)

    def test_cancel_mid_turn_stops_before_the_next_model_call_and_keeps_nothing(self):
        c = self.make()
        sid = c.worker.start_session("m_100", ["support"], "customer")
        calls = Counting(c.chatbot.engine(), on_first=lambda: c.worker.cancel(sid, "msg-0001"))
        r = c.worker.handle(sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertEqual(r["blocked_by"], "cancelled")
        self.assertEqual(calls.calls, 1)                 # the tool round's result never reached a second call
        _, state = c.store.load(sid)
        self.assertIsNone(state["memory"]["focus"])      # the pre-turn state was committed
        nxt = c.worker.handle(sid, "msg-0002", f"why did {DECLINED} fail?")
        self.assertIsNone(nxt["blocked_by"])             # the session works normally afterwards

    def test_works_on_the_dynamodb_store(self):
        clock = Clock()
        c = self.make(store=DynamoSessionStore(InMemoryDynamo(), clock=clock))
        sid = c.worker.start_session("m_100", ["support"], "customer")
        c.worker.cancel(sid, "msg-0001")
        self.assertEqual(c.worker.handle(sid, "msg-0001", "hi there")["blocked_by"], "cancelled")


class ServiceAndHttp(unittest.TestCase):
    def setUp(self):
        self.c = container(Clock())
        self.addCleanup(self.c.close)
        v = self.c.verifier
        self.mine = v.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
        self.other = v.issue({"sub": "u2", "tenant_id": "m_200", "role": "customer"})

    def test_only_the_session_owner_can_cancel(self):
        sid = self.c.service.start(self.mine)
        with self.assertRaises(Forbidden):
            self.c.service.cancel(self.other, sid, "msg-0001")
        self.assertEqual(self.c.service.cancel(self.mine, sid, "msg-0001"), {"cancelled": "msg-0001"})

    def test_http_route(self):
        sid = self.c.service.start(self.mine)
        body = json.dumps({"msg_id": "msg-0001"}).encode()
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": f"/v1/sessions/{sid}/cancel",
               "HTTP_AUTHORIZATION": f"Bearer {self.mine}", "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        status = []
        out = b"".join(App(self.c.service)(env, lambda s, h: status.append(s)))
        self.assertTrue(status[0].startswith("202"), status)
        self.assertEqual(json.loads(out), {"cancelled": "msg-0001"})


class FinalCallCancellation(unittest.TestCase):
    def test_cancel_during_final_call_discards_state_and_deduplicates_on_both_stores(self):
        from finbot.store import SessionStore

        for factory in (SessionStore, lambda **kw: DynamoSessionStore(InMemoryDynamo(), **kw)):
            clock = Clock()
            c = container(clock, store=factory(clock=clock))
            self.addCleanup(c.close)
            sid = c.worker.start_session("m_100", ["support"], "customer")
            before = c.store.load(sid)[1]
            engine = c.chatbot.engine()
            original_create = engine.create
            calls = []

            def cancel_final(*args, create=original_create, seen=calls, worker=c.worker, session_id=sid, **kw):
                result = create(*args, **kw)
                seen.append(result.stop_reason)
                if result.stop_reason != "tool_use":
                    worker.cancel(session_id, "msg-0001")
                return result

            engine.create = cancel_final
            result = c.worker.handle(sid, "msg-0001", f"why did {DECLINED} fail?")
            self.assertEqual(result["blocked_by"], "cancelled")
            self.assertEqual(c.store.load(sid)[1], before)
            self.assertGreater(len(calls), 1)
            count = len(calls)
            again = c.worker.handle(sid, "msg-0001", "retry")
            self.assertTrue(again["deduplicated"])
            self.assertEqual(again["answer"], replies.CANCELLED)
            self.assertEqual(len(calls), count)


if __name__ == "__main__":
    unittest.main()
