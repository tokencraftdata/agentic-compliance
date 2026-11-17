"""Queue dispatch (finbot/dispatch.py): in queue mode the API never runs a turn or calls a model.
It enqueues, waits for a worker's commit, and answers "pending" (202) when the commit takes
longer; resending the same msg_id is safe and returns the committed answer.
"""
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_layers import DECLINED, Clock, container  # noqa: E402

from finbot.config import Settings  # noqa: E402
from finbot.container import Container  # noqa: E402
from finbot.dispatch import QueueDispatcher  # noqa: E402
from finbot.http_api import App  # noqa: E402
from finbot.queue import InMemoryQueue, QueueConsumer  # noqa: E402
from finbot.runtime import Worker  # noqa: E402
from finbot.store import SessionStore  # noqa: E402


class Counter:
    def __init__(self, engine):
        self.calls, self.create = 0, engine.create
        engine.create = self

    def __call__(self, *a, **k):
        self.calls += 1
        return self.create(*a, **k)


class QueueMode(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = SessionStore(clock=self.clock)
        self.addCleanup(self.store.close)
        self.api = container(self.clock, store=self.store)           # API side
        self.workers = container(self.clock, store=self.store)       # worker side, same store
        self.queue = InMemoryQueue(clock=self.clock)
        self.sent = []
        send = self.queue.send
        self.queue.send = lambda body: (self.sent.append(body["msg_id"]), send(body))[1]
        self.consumer = QueueConsumer(self.queue, Worker(self.workers.chatbot, self.store, "w1"))
        self.api_calls = Counter(self.api.chatbot.engine())
        self.waits = 0

        def sleep(_s):          # each poll of the API gives the worker pool one chance to run
            self.waits += 1
            if self.run_workers:
                self.consumer.poll_once()
        self.run_workers = True
        self.dispatch = QueueDispatcher(self.queue, self.store, wait_s=5, poll_s=0.5, sleep=sleep,
                                        clock=self._mono)
        self.api.chatbot  # noqa: B018
        self.api.service.dispatch = self.dispatch
        v = self.api.verifier
        self.token = v.issue({"sub": "u1", "tenant_id": "m_100", "role": "customer"})
        self.sid = self.api.service.start(self.token)
        self.t = 0.0

    def _mono(self):
        self.t += 0.5
        return self.t

    def test_the_api_never_runs_the_turn(self):
        r = self.api.service.send(self.token, self.sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertIn("declined", r["answer"])
        self.assertFalse(r["deduplicated"])
        self.assertEqual(self.api_calls.calls, 0)                     # all model calls were the worker's
        self.assertEqual(self.sent, ["msg-0001"])

    def test_pending_when_no_worker_commits_in_time_then_resend_gets_it(self):
        self.run_workers = False
        r = self.api.service.send(self.token, self.sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertTrue(r["pending"])
        self.consumer.poll_once()                                     # a worker picks it up later
        again = self.api.service.send(self.token, self.sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertIn("declined", again["answer"])
        self.assertTrue(again["deduplicated"])
        self.assertEqual(self.sent, ["msg-0001"])                     # answered from the store, not re-enqueued

    def test_an_api_crash_after_enqueue_loses_nothing(self):
        self.run_workers = False
        self.dispatch.wait_s = 0                                      # the API "dies" right after sending
        self.api.service.send(self.token, self.sid, "msg-0001", f"why did {DECLINED} fail?")
        self.consumer.poll_once()
        self.dispatch.wait_s = 5
        r = self.api.service.send(self.token, self.sid, "msg-0001", f"why did {DECLINED} fail?")
        self.assertIn("declined", r["answer"])

    def test_http_returns_202_with_retry_after(self):
        self.run_workers = False
        body = json.dumps({"msg_id": "msg-0001", "text": f"why did {DECLINED} fail?"}).encode()
        env = {"REQUEST_METHOD": "POST", "PATH_INFO": f"/v1/sessions/{self.sid}/messages",
               "HTTP_AUTHORIZATION": f"Bearer {self.token}", "CONTENT_TYPE": "application/json",
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        seen = []
        out = json.loads(b"".join(App(self.api.service)(env, lambda s, h: seen.append((s, dict(h))))))
        self.assertTrue(seen[0][0].startswith("202"))
        self.assertEqual(seen[0][1].get("Retry-After"), "2")
        self.assertTrue(out["pending"])


class Wiring(unittest.TestCase):
    def test_queue_mode_needs_a_queue(self):
        c = Container(Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent",
                               data_dir="", store_path=":memory:", dispatch="queue"))
        self.addCleanup(c.close)
        with self.assertRaises(ValueError):
            _ = c.dispatcher

    def test_inline_is_the_default(self):
        c = container(Clock())
        self.addCleanup(c.close)
        self.assertEqual(c.service.dispatch.mode, "inline")


if __name__ == "__main__":
    unittest.main()
