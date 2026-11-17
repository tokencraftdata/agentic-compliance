"""How the API gets a turn run: in its own process (inline) or by a worker (queue).

    inline   ChatService calls Worker.handle. One process does everything: dev, tests, a single box.
    queue    the API never runs a turn and never calls a model. It returns the committed answer if
             the message already has one; otherwise it enqueues {session_id, msg_id, tenant_id,
             text} on the FIFO turn queue (group = session, dedup = msg_id) and polls the session
             store for the commit, up to `wait_s`. If no commit arrives in time it returns
             "pending" (HTTP 202) and the client resends the same msg_id later, which is safe:
             the queue deduplicates by msg_id and the worker's journal returns a committed turn
             instead of running it again.

Queue mode is what the production design (docs/design.md section 11) assumes: API pods need only
`sqs:SendMessage` and read access to session items, and an API pod that dies mid-request loses
nothing, because the turn lives in the queue, not in the pod.
"""
from __future__ import annotations

import time
from collections.abc import Callable


class InlineDispatcher:
    mode = "inline"

    def __init__(self, worker):
        self.worker = worker

    def run(self, tenant_id: str, session_id: str, msg_id: str, text: str) -> dict:
        return self.worker.handle(session_id, msg_id, text)


class QueueDispatcher:
    mode = "queue"

    def __init__(self, queue, store, wait_s: float = 25.0, poll_s: float = 0.2,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.queue, self.store, self.wait_s, self.poll_s = queue, store, wait_s, poll_s
        self.sleep, self.clock = sleep, clock

    def run(self, tenant_id: str, session_id: str, msg_id: str, text: str) -> dict:
        done = self.store.reply(session_id, msg_id)
        if done is not None:                       # a resend of a committed turn: answer at once
            return {**done, "deduplicated": True}
        self.queue.send({"session_id": session_id, "msg_id": msg_id, "tenant_id": tenant_id, "text": text})
        end = self.clock() + self.wait_s
        while self.clock() < end:
            self.sleep(self.poll_s)
            done = self.store.reply(session_id, msg_id)
            if done is not None:
                return {**done, "deduplicated": False}
        return {"pending": True, "msg_id": msg_id, "retry_after_s": 2,
                "answer": None, "note": "still working on it; resend the same msg_id to get the answer"}
