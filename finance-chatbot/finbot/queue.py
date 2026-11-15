"""Queue worker: run Workers behind a durable queue (SQS in production).

    API (ChatService) --enqueue--> SQS --receive--> QueueConsumer -> Worker.handle
                                                          |
                         answer committed -> ack; client polls / gets a push

Delivery is at-least-once, which is safe because the turn journal dedupes by
msg_id and the session commit is a compare-and-swap:

    outcome of handle()                 queue action
    ----------------------------------  -----------------------------------------
    committed (or deduplicated)         ack (delete)
    LeaseHeld (another worker owns it)  retry_later(short): it will be free soon
    dependency unavailable              retry_later(retry_after_s): same msg_id
    StaleWrite (superseded mid-turn)    ack: the newer owner finishes the turn
    process crash / OOM                 nothing: visibility timeout redelivers
    unexpected error                    retry_later; dead-letter after max_receives

The visibility timeout must exceed the turn deadline (turn_deadline_s) so a
healthy slow turn is not redelivered to a second worker while still running;
the lease + CAS commit is the backstop if it is.

Messages are authorized at the API before enqueue AND re-checked here
(tenant_id in the body must match the session), so a forged queue message
cannot cross tenants.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .runtime import Worker
from .store import LeaseHeld, SessionExpired, StaleWrite

log = logging.getLogger("finbot.queue")


def _masked(body: dict) -> dict:
    """A queue body with card data masked (DLQ copies are kept for days and read by people)."""
    from .guardrails import mask_pii
    return {**body, "text": mask_pii(body["text"])[0]} if isinstance(body.get("text"), str) else body


@dataclass
class Message:
    body: dict
    receive_count: int = 0
    handle: str = ""
    visible_at: float = 0.0


class InMemoryQueue:
    """Same semantics as SQS (visibility timeout, receive count, DLQ), for tests and local runs."""

    def __init__(self, visibility_s: float = 120, clock: Callable[[], float] = time.time):
        self.items: list[Message] = []
        self.dlq: list[tuple[Message, str]] = []
        self.visibility_s, self.clock = visibility_s, clock

    def send(self, body: dict) -> None:
        self.items.append(Message(_masked(body), handle=str(len(self.items))))

    def receive(self, max_messages: int = 10) -> list[Message]:
        now = self.clock()
        out: list[Message] = []
        for m in self.items:
            if len(out) >= max_messages:
                break
            if m.visible_at <= now:
                m.receive_count += 1
                m.visible_at = now + self.visibility_s
                out.append(m)
        return out

    def ack(self, m: Message) -> None:
        self.items.remove(m)

    def retry_later(self, m: Message, delay_s: float) -> None:
        m.visible_at = self.clock() + delay_s

    def dead_letter(self, m: Message, reason: str) -> None:
        self.items.remove(m)
        self.dlq.append((m, reason))


class SqsQueue:
    """Adapter for Amazon SQS (configure a redrive policy to a DLQ as well)."""

    def __init__(self, queue_url: str, dlq_url: str, region: str, visibility_s: int = 120):
        import boto3  # lazy
        self.sqs, self.url, self.dlq_url, self.visibility_s = boto3.client("sqs", region_name=region), \
            queue_url, dlq_url, visibility_s

    def send(self, body: dict) -> None:
        # FIFO queue keyed by session keeps one session's turns in order.
        # Masked at enqueue: the queue never holds card data, even for the seconds before a worker.
        self.sqs.send_message(QueueUrl=self.url, MessageBody=json.dumps(_masked(body)),
                              MessageGroupId=body["session_id"], MessageDeduplicationId=body["msg_id"])

    def receive(self, max_messages: int = 10) -> list[Message]:
        r = self.sqs.receive_message(QueueUrl=self.url, MaxNumberOfMessages=max_messages, WaitTimeSeconds=20,
                                     VisibilityTimeout=self.visibility_s,
                                     MessageSystemAttributeNames=["ApproximateReceiveCount"])
        return [Message(json.loads(m["Body"]), int(m["Attributes"]["ApproximateReceiveCount"]), m["ReceiptHandle"])
                for m in r.get("Messages", [])]

    def ack(self, m: Message) -> None:
        self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=m.handle)

    def retry_later(self, m: Message, delay_s: float) -> None:
        self.sqs.change_message_visibility(QueueUrl=self.url, ReceiptHandle=m.handle,
                                           VisibilityTimeout=int(min(43200, max(0, delay_s))))

    def dead_letter(self, m: Message, reason: str) -> None:
        # A FIFO queue's DLQ must also be FIFO, and FIFO sends need a group and a dedup id.
        fifo = {"MessageGroupId": m.body.get("session_id", "dlq"),
                "MessageDeduplicationId": f"dlq-{m.body.get('msg_id', m.handle)[:120]}"} \
            if self.dlq_url.endswith(".fifo") else {}
        body = json.dumps({**_masked(m.body), "dlq_reason": reason})
        self.sqs.send_message(QueueUrl=self.dlq_url, MessageBody=body, **fifo)
        self.ack(m)


@dataclass
class ConsumerStats:
    acked: int = 0
    retried: int = 0
    dead_lettered: int = 0
    results: list = field(default_factory=list)


class QueueConsumer:
    def __init__(self, source, worker: Worker, max_receives: int = 5,
                 on_result: Callable[[dict, dict], None] | None = None):
        self.source, self.worker, self.max_receives = source, worker, max_receives
        self.on_result = on_result or (lambda body, result: None)  # e.g. push to a websocket / write to a table
        self.stats = ConsumerStats()

    def poll_once(self) -> int:
        msgs = list(self.source.receive())
        for m in msgs:
            self._process(m)
        return len(msgs)

    def _process(self, m: Message) -> None:
        b = m.body
        if m.receive_count > self.max_receives:
            self.source.dead_letter(m, f"failed {m.receive_count - 1} deliveries")
            self.stats.dead_lettered += 1
            return
        try:
            if self.worker.store.tenant_of(b["session_id"]) != b["tenant_id"]:
                self.source.dead_letter(m, "tenant mismatch")  # forged or corrupted message: never process
                self.stats.dead_lettered += 1
                return
            r = self.worker.handle(b["session_id"], b["msg_id"], b["text"])   # handle() masks card data
        except (KeyError, SessionExpired) as e:
            self.source.dead_letter(m, f"session gone: {type(e).__name__}")
            self.stats.dead_lettered += 1
            return
        except LeaseHeld:
            self.source.retry_later(m, 5)
            self.stats.retried += 1
            return
        except StaleWrite:
            self.source.ack(m)  # a newer owner has (or will) finish this turn
            self.stats.acked += 1
            return
        except Exception:  # noqa: BLE001 - keep the consumer alive; redeliver, then DLQ
            log.exception("turn failed for session %s msg %s", b.get("session_id"), b.get("msg_id"))
            self.source.retry_later(m, min(300, 10 * 2 ** m.receive_count))
            self.stats.retried += 1
            return
        if r.get("blocked_by") == "unavailable":
            self.source.retry_later(m, r.get("retry_after_s", 30))
            self.stats.retried += 1
            return
        self.on_result(b, r)
        self.stats.results.append(r)
        self.source.ack(m)
        self.stats.acked += 1
