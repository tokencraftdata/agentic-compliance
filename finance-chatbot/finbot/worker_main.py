"""Queue worker process, one per container:  python -m finbot.worker_main

Long-polls the queue (SQS FIFO, grouped by session) and runs each message through
QueueConsumer -> Worker.handle. Built to be stopped and killed safely:

  * One message at a time. Concurrency comes from more pods, so a pod never holds
    a batch of turns it would have to abandon on shutdown.
  * SIGTERM (rollout, scale-in, node drain): stop receiving, finish the turn in
    hand, exit. Set terminationGracePeriodSeconds above turn_deadline_s (90 s).
  * SIGKILL / OOM / node loss: nothing to clean up. The turn was never committed,
    the message becomes visible again after the visibility timeout, and another
    pod re-runs it from the last committed state once the session lease lapses.
  * Liveness: the heartbeat file is touched after every poll (an empty long poll
    returns within 20 s); the probe fails if it goes stale.
  * Refuses to start if the visibility timeout does not exceed the turn deadline:
    otherwise a healthy slow turn would be redelivered to a second pod mid-turn.

Environment (besides the usual FINBOT_* settings):
  FINBOT_QUEUE_URL, FINBOT_DLQ_URL   SQS queue and dead-letter queue (both .fifo)
  FINBOT_VISIBILITY_S                default 150
  FINBOT_HEARTBEAT_FILE              default /tmp/finbot-heartbeat
"""
from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .queue import Message, QueueConsumer

log = logging.getLogger("finbot.worker")


class OneAtATime:
    """Queue view that hands out at most one message per poll, and none once stopping."""

    def __init__(self, source: Any, stopping: Callable[[], bool]):
        self.source, self.stopping = source, stopping

    def receive(self, max_messages: int = 1) -> list[Message]:
        return [] if self.stopping() else list(self.source.receive(max_messages=1))

    def __getattr__(self, name: str) -> Any:  # ack, retry_later, dead_letter
        return getattr(self.source, name)


HEARTBEAT = "/tmp/finbot-heartbeat"   # noqa: S108 - liveness file on the pod-private emptyDir # nosec B108


class Runner:
    def __init__(self, consumer_factory: Callable[[OneAtATime], QueueConsumer], source: Any,
                 heartbeat: str = HEARTBEAT, idle_sleep_s: float = 0.0):
        self.stopping = False
        self.consumer = consumer_factory(OneAtATime(source, lambda: self.stopping))
        self.heartbeat, self.idle_sleep_s = Path(heartbeat), idle_sleep_s

    def stop(self, *_: Any) -> None:
        if not self.stopping:
            log.info("stop requested: finishing the turn in hand, then exiting")
        self.stopping = True

    def run(self, max_polls: int | None = None) -> int:
        polls = 0
        while not self.stopping and (max_polls is None or polls < max_polls):
            got = self.consumer.poll_once()
            self.heartbeat.touch()
            polls += 1
            if not got and self.idle_sleep_s:
                time.sleep(self.idle_sleep_s)
        return polls


def check_timeouts(visibility_s: float, turn_deadline_s: float, margin_s: float = 30) -> None:
    if visibility_s < turn_deadline_s + margin_s:
        raise SystemExit(f"FINBOT_VISIBILITY_S={visibility_s:g} must be at least turn_deadline_s + "
                         f"{margin_s:g} = {turn_deadline_s + margin_s:g}")


def main() -> None:  # pragma: no cover - wiring only; Runner is tested
    from .config import Settings
    from .container import Container
    from .queue import SqsQueue

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    s = Settings()
    visibility = float(os.environ.get("FINBOT_VISIBILITY_S", "150"))
    check_timeouts(visibility, s.turn_deadline_s)
    if not s.queue_url or not os.environ.get("FINBOT_DLQ_URL"):
        raise SystemExit("FINBOT_QUEUE_URL and FINBOT_DLQ_URL are required")
    c = Container(s, audit_path=os.environ.get("FINBOT_AUDIT"))
    source = SqsQueue(s.queue_url, os.environ["FINBOT_DLQ_URL"], s.aws_region, int(visibility))
    runner = Runner(lambda q: QueueConsumer(q, c.worker, s.max_receives), source,
                    os.environ.get("FINBOT_HEARTBEAT_FILE", HEARTBEAT))
    signal.signal(signal.SIGTERM, runner.stop)
    signal.signal(signal.SIGINT, runner.stop)
    log.info("worker %s polling %s", c.worker.id, s.queue_url)
    runner.run()
    c.close()
    log.info("worker %s stopped cleanly", c.worker.id)


if __name__ == "__main__":
    main()
