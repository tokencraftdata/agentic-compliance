"""Load test: how many turns finbot's own code can carry, and what a real model changes.

Runs the full worker path (lease, journal, turn pipeline, tools, gate, compare-and-swap commit)
with the scripted model, in N worker processes at once, each with its own sessions, the way
pods run in production (one turn in flight per worker).

    python3 scripts/load_test.py --workers 4 --turns 300
    python3 scripts/load_test.py --workers 8 --turns 50 --model-latency 4

--model-latency adds a sleep per model call to stand in for Bedrock. With it, throughput is set
by the model, not by finbot's code: a worker does about 1 / (calls per turn x latency) turns
per second. Live latency has not been measured; pick a value from your own Bedrock metrics.

What this does NOT measure: Bedrock quotas, DynamoDB and SQS under load, network, or the
guardrail service. Those need the real stack.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import resource
import statistics
import sys
import time
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _cases(data_dir: str) -> list[tuple[str, str]]:
    out = []
    with open(os.path.join(data_dir, "eval.jsonl")) as f:
        lines = f.readlines()
    for line in lines:
        c = json.loads(line)
        turns = c.get("turns") or [c.get("question", "")]
        tenant = c.get("tenant") or c.get("tenant_id") or "m_100"
        out += [(tenant, t) for t in turns if t]
    return out


def _work(args) -> dict:
    idx, turns, data_dir, latency = args
    from finbot.config import Settings
    from finbot.container import Container
    s = Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent", data_dir=data_dir)
    c = Container(s)
    bot, worker = c.chatbot, c.worker
    if latency > 0:                              # stand-in for a real model's response time
        engine = bot.engine()
        create = engine.create

        def slow(*a, **k):
            time.sleep(latency)
            return create(*a, **k)
        engine.create = slow
    cases = _cases(data_dir)
    lat, calls, blocked = [], 0, 0
    sessions: dict[str, str] = {}
    t0, cpu0 = time.perf_counter(), time.process_time()
    for i in range(turns):
        tenant, text = cases[(idx * 7919 + i) % len(cases)]
        sid = sessions.get(tenant) if i % 5 else None   # a new conversation every 5 turns
        if sid is None:
            sid = sessions[tenant] = worker.start_session(tenant, ["support"], "customer")
        t = time.perf_counter()
        r = worker.handle(sid, uuid.uuid4().hex[:16], text)
        lat.append(time.perf_counter() - t)
        calls += len(r.get("models") or [])
        blocked += r.get("blocked_by") is not None
    return {"lat": lat, "wall": time.perf_counter() - t0, "cpu": time.process_time() - cpu0,
            "calls": calls, "blocked": blocked,
            "rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin"
                                                                           else 1024)}


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--turns", type=int, default=200, help="turns per worker")
    ap.add_argument("--data", default="data")
    ap.add_argument("--model-latency", type=float, default=0.0, help="seconds added per model call")
    a = ap.parse_args()
    t0 = time.perf_counter()
    with mp.Pool(a.workers) as pool:
        res = pool.map(_work, [(i, a.turns, a.data, a.model_latency) for i in range(a.workers)])
    wall = time.perf_counter() - t0
    lat = [x for r in res for x in r["lat"]]
    n = len(lat)
    calls = sum(r["calls"] for r in res)
    cpu_ms = 1000 * sum(r["cpu"] for r in res) / n
    print(f"workers {a.workers}  turns {n}  model latency {a.model_latency:g}s per call")
    print(f"throughput      {n / wall:8.1f} turns/s  ({n / wall / a.workers:.1f} per worker)")
    print(f"turn latency    p50 {pct(lat, .5) * 1000:7.1f} ms   p95 {pct(lat, .95) * 1000:7.1f} ms   "
          f"p99 {pct(lat, .99) * 1000:7.1f} ms   mean {statistics.mean(lat) * 1000:.1f} ms")
    print(f"CPU per turn    {cpu_ms:8.1f} ms   model calls per turn {calls / n:.2f}   "
          f"answered without a block {100 * (1 - sum(r['blocked'] for r in res) / n):.0f}%")
    print(f"peak memory     {max(r['rss_mb'] for r in res):8.0f} MB per worker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
