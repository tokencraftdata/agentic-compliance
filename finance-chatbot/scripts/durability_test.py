"""Durability test: real processes, a shared session store, real kills.

The unit tests prove each recovery path with fakes in one process. This script runs the same
paths the way production fails: separate worker processes on one SQLite file, SIGKILL in the
middle of a turn, SIGSTOP to make a worker freeze past its lease, a model that is down, and an
ingestion run killed half-way.

    python3 scripts/durability_test.py            # all scenarios
    python3 scripts/durability_test.py crash zombie

Scenarios
    crash       a worker is killed mid-turn; a new process takes over after the lease and
                finishes the turn exactly once
    lost_reply  a worker commits and dies before replying; the resend returns the stored
                answer with no model call
    zombie      a worker freezes past its lease; another finishes the turn; the frozen one
                wakes up and its late commit is refused; reconciling the audit log against
                committed turns shows the turn once committed, its re-run named, and no gaps
    memory      32 turns, each in a fresh process, switching between two payments with
                follow-ups ("can I retry it?"): the follow-up resolves to the right payment
                every time, across thread rollover
    failover    the primary model is down: the backup answers, the breaker opens and is
                shared, so a new process skips the primary without calling it
    contention  6 worker processes at once with a slow model and the CPU saturated: 4 on their
                own sessions, 2 racing on one session; with queue-style redelivery, every message
                is committed exactly once and versions have no gaps (heartbeats keep slow turns'
                leases alive; a turn that does overrun is the zombie scenario)
    guard_down  the guardrail service is down: no model call, nothing committed; the same
                message resent later gets a real answer
    ingestion   an ingestion run is killed half-way; the index on disk is still valid, the
                re-run finishes the rest, and a third run changes nothing

Scripted model, synthetic data, local SQLite: this shows the logic holds under real process
failure, not that DynamoDB, SQS or Bedrock behave the same way.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
PY = sys.executable
LEASE = 2.0


def settings(db: str, **kw):
    from finbot.config import Settings
    return Settings(llm_provider="fake", guardrail_provider="local", kb_path="/nonexistent", data_dir="data",
                    store_provider="sqlite", store_path=db, lease_s=LEASE, **kw)


def container(db: str, **over):
    from finbot.container import Container
    # Audit lines go next to the database, so a scenario can reconcile them with committed turns.
    c = Container(settings(db), overrides=over or None, audit_path=db + ".audit.jsonl")
    c.chatbot.sleep = c.toolbox.sleep = lambda s: None
    return c


def failed_txns(tenant: str = "m_100", n: int = 2) -> list[str]:
    with open(os.path.join(ROOT, "data", "transactions.jsonl")) as f:
        rows = [json.loads(line) for line in f]
    return [r["txn_id"] for r in rows if r["tenant_id"] == tenant and r["status"] == "declined"][:n]


def child(*args: str, wait: bool = True, timeout: float = 120):
    cmd = [PY, os.path.abspath(__file__), "--child", *args]
    if not wait:
        # this script re-running itself with fixed arguments: no outside input reaches the command
        return subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # noqa: S603
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    lines = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"child {args[0]} printed nothing (exit {r.returncode}): {r.stderr[-800:]}")
    return json.loads(lines[-1])


REFUSED = {"late_commits": 0, "lease_waits": 0}


def handle_with_retry(worker, sid: str, msg: str, text: str, limit_s: float = 30) -> dict:
    """What queue redelivery does: if another worker holds the session, or this worker's turn
    ran past its lease and its commit was refused (StaleWrite), deliver the same message again.
    The journal makes the retry safe: a turn already committed is returned, not re-run."""
    from finbot.store import LeaseHeld, StaleWrite
    end = time.time() + limit_s
    while True:
        try:
            return worker.handle(sid, msg, text)
        except (LeaseHeld, StaleWrite) as e:
            REFUSED["late_commits" if isinstance(e, StaleWrite) else "lease_waits"] += 1
            if time.time() > end:
                raise
            time.sleep(0.25)


def slow_create(engine, seconds: float, counter: list | None = None):
    create = engine.create

    def wrapped(*a, **k):
        if counter is not None:
            counter.append(1)
        time.sleep(seconds)
        return create(*a, **k)
    engine.create = wrapped


# ------------------------------------------------------------------ child roles

def run_child(role: str, args: list[str]) -> None:
    out: dict = {}
    if role == "start":
        db, tenant = args
        c = container(db)
        out = {"sid": c.worker.start_session(tenant, ["support"], "customer")}
    elif role == "turn":                       # one turn in a fresh process
        db, sid, msg, text = args
        c = container(db)
        calls: list = []
        slow_create(c.chatbot.engine(), 0, calls)
        out = {**handle_with_retry(c.worker, sid, msg, text), "model_calls": len(calls)}
    elif role == "hang":                       # the model never returns: killed from outside
        db, sid, msg, text = args
        c = container(db)
        slow_create(c.chatbot.engine(), 3600)
        c.worker.handle(sid, msg, text)
    elif role == "commit_then_die":
        db, sid, msg, text = args
        c = container(db)
        c.worker.handle(sid, msg, text)
        os._exit(9)                            # crash after commit, before any reply leaves
    elif role == "slow":                       # model takes longer than the lease
        db, sid, msg, text, seconds = args
        c = container(db)
        slow_create(c.chatbot.engine(), float(seconds))
        try:
            out = {"result": c.worker.handle(sid, msg, text)}
        except Exception as e:                 # noqa: BLE001 - the error IS the result here
            out = {"error": type(e).__name__}
    elif role == "failover":
        db, sid, msg, text = args
        from finbot.faults import OutageLLM
        from finbot.llm import FakeLLM
        from finbot.models import ModelOrchestrator, ModelTarget
        primary, backup = OutageLLM(FakeLLM(), down=True), OutageLLM(FakeLLM())
        engine = ModelOrchestrator([ModelTarget("primary", primary, attempts=1),
                                    ModelTarget("backup", backup, attempts=1)],
                                   sleep=lambda s: None, breaker_threshold=2, cooldown_s=60)
        c = container(db, engine=engine)
        r = handle_with_retry(c.worker, sid, msg, text)
        out = {"answer": r["answer"], "blocked_by": r["blocked_by"], "models": r["models"],
               "primary_attempts": primary.attempts, "backup_calls": backup.calls,
               "primary_state": engine.strong[0].circuit.state}
    elif role == "burst":                      # many turns, one process; slow model, so some overrun the lease
        db, sid, prefix, n, text = args
        import random
        c = container(db)
        engine, create = c.chatbot.engine(), c.chatbot.engine().create

        def slow(*a, **k):
            time.sleep(random.uniform(0, 1.3))   # noqa: S311 - test jitter, not security
            return create(*a, **k)
        engine.create = slow
        for i in range(int(n)):
            handle_with_retry(c.worker, sid, f"{prefix}-{i:04d}", text, limit_s=120)
        out = {"done": int(n), **REFUSED}
    elif role == "ingest":
        src, dst, delay = args
        from finbot.ingestion import HeadingChunker, IngestionPipeline, LocalVectorStore
        from finbot.ingestion.embedders import HashingEmbedder
        from finbot.ingestion.sources import LocalSource

        class Slow(HashingEmbedder):
            def embed_documents(self, texts):
                time.sleep(float(delay))
                return super().embed_documents(texts)
        rep = IngestionPipeline(LocalSource(src), HeadingChunker(), Slow(), LocalVectorStore(dst)).run()
        out = {"indexed": rep.indexed_docs, "skipped": rep.skipped_unchanged,
               "quarantined": [q[0] for q in rep.quarantined], "failed": rep.failed}
    print(json.dumps(out, default=str), flush=True)


# ------------------------------------------------------------------ scenarios (parent)

def store(db: str):
    from finbot.store import SessionStore
    return SessionStore(db)


def committed(db: str, sid: str) -> list[dict]:
    s = store(db)
    try:
        return [t for t in s.turns(sid) if t["status"] == "committed"]
    finally:
        s.close()


def version(db: str, sid: str) -> int:
    s = store(db)
    try:
        return s.load(sid)[0]
    finally:
        s.close()


def scenario_crash(db):
    txn = failed_txns()[0]
    sid = child("start", db, "m_100")["sid"]
    p = child("hang", db, sid, "msg-crash-0001", f"why did {txn} fail?", wait=False)
    time.sleep(1.5)
    p.send_signal(signal.SIGKILL)
    p.wait()
    t0 = time.time()
    r = child("turn", db, sid, "msg-crash-0001", f"why did {txn} fail?")
    turns = committed(db, sid)
    ok = ("declined" in r["answer"] and len(turns) == 1 and turns[0]["attempts"] == 2 and version(db, sid) == 1)
    return ok, (f"killed mid-turn; replacement finished after {time.time() - t0:.1f}s (lease {LEASE:g}s); "
                f"committed once, journal attempts={turns[0]['attempts'] if turns else 0}, version={version(db, sid)}")


def scenario_lost_reply(db):
    txn = failed_txns()[0]
    sid = child("start", db, "m_100")["sid"]
    try:
        child("commit_then_die", db, sid, "msg-lost-0001", f"why did {txn} fail?")
    except RuntimeError:
        pass                                   # it died without printing, as intended
    r = child("turn", db, sid, "msg-lost-0001", f"why did {txn} fail?")
    ok = r.get("deduplicated") is True and r["model_calls"] == 0 and version(db, sid) == 1
    return ok, (f"resend after a crash-after-commit: deduplicated={r.get('deduplicated')}, "
                f"model calls={r['model_calls']}")


def scenario_zombie(db):
    txn = failed_txns()[0]
    sid = child("start", db, "m_100")["sid"]
    z = child("slow", db, sid, "msg-zombie-01", f"why did {txn} fail?", "4", wait=False)
    time.sleep(0.8)
    z.send_signal(signal.SIGSTOP)              # frozen: GC pause, network partition
    time.sleep(LEASE + 1)
    r = child("turn", db, sid, "msg-zombie-01", f"why did {txn} fail?")
    z.send_signal(signal.SIGCONT)
    out, _ = z.communicate(timeout=60)
    late = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
    from finbot.reconcile import reconcile_audit
    s = store(db)
    try:
        with open(db + ".audit.jsonl") as f:
            rep = reconcile_audit(f, s, [sid])
    finally:
        s.close()
    ok = "declined" in r["answer"] and late.get("error") in ("LeaseHeld", "StaleWrite") and \
        len(committed(db, sid)) == 1 and version(db, sid) == 1 and not rep.problems
    return ok, (f"replacement committed; the frozen worker woke and was refused with {late.get('error')}; "
                f"audit reconciliation: {rep.counts}, no gaps")


def scenario_memory(db):
    a, b = failed_txns(n=2)
    sid = child("start", db, "m_100")["sid"]
    wrong, focus = [], None
    for i in range(32):
        if i % 4 == 0:
            focus = a if (i // 4) % 2 == 0 else b
            text = f"why did {focus} fail?"
        else:
            text = ["can I retry it?", "what should I tell the customer about it?", "is it still declined?"][i % 4 - 1]
        r = child("turn", db, sid, f"msg-mem-{i:04d}", text)
        if r.get("focus") != focus:
            wrong.append((i, text, r.get("focus")))
    s = store(db)
    try:
        state = s.load(sid)[1]
    finally:
        s.close()
    mem = state["memory"]
    ok = not wrong and mem["turns_in_thread"] < 32 and mem.get("recap") and {a, b} <= set(mem["user_ids"])
    return ok, (f"32 turns, a new process each: follow-ups resolved to the right payment {32 - len(wrong)}/32; "
                f"thread rolled over (turns in current thread {mem['turns_in_thread']}), recap kept "
                f"{len(mem.get('recap') or [])} requests" + (f"; wrong: {wrong[:3]}" if wrong else ""))


def scenario_failover(db):
    txn = failed_txns()[0]
    sid = child("start", db, "m_100")["sid"]
    first = child("failover", db, sid, "msg-fo-0001", f"why did {txn} fail?")
    first2 = child("failover", db, sid, "msg-fo-0002", f"why did {txn} fail?")
    sid2 = child("start", db, "m_100")["sid"]
    second = child("failover", db, sid2, "msg-fo-0003", f"why did {txn} fail?")
    ok = ("declined" in first["answer"] and "backup" in first["models"] and "primary" not in first["models"]
          and first2["primary_state"] == "open" and second["primary_attempts"] == 0
          and "declined" in second["answer"])
    return ok, (f"primary down: backup answered (models {first['models']}); breaker {first2['primary_state']} after "
                f"repeated failures; a new process tried the primary {second['primary_attempts']} times")


def scenario_contention(db):
    txn = failed_txns()[0]
    own = [child("start", db, "m_100")["sid"] for _ in range(4)]
    # Starve the CPU on purpose: the same guarantees must hold when every process runs slowly.
    burners = [subprocess.Popen([PY, "-c", "while True: pass"]) for _ in range(os.cpu_count() or 4)]  # noqa: S603
    shared = child("start", db, "m_100")["sid"]
    n = 10
    procs = [child("burst", db, sid, f"own{i}", str(n), f"why did {txn} fail?", wait=False)
             for i, sid in enumerate(own)]
    procs += [child("burst", db, shared, f"race{i}", str(n), f"why did {txn} fail?", wait=False) for i in range(2)]
    t0 = time.time()
    errors, late, waits = [], 0, 0
    try:
        for p in procs:
            out, err = p.communicate(timeout=600)
            if p.returncode:
                errors.append(err.strip().splitlines()[-1] if err.strip() else f"exit {p.returncode}")
                continue
            r = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
            late, waits = late + r["late_commits"], waits + r["lease_waits"]
    finally:
        for b in burners:
            b.kill()
            b.wait()
    problems: list = [("child", e) for e in errors]
    for sid, want in [*((s, n) for s in own), (shared, 2 * n)]:
        turns = committed(db, sid)
        if len(turns) != want or len({t["msg_id"] for t in turns}) != want or version(db, sid) != want:
            problems.append((sid[-6:], len(turns), version(db, sid)))
    return not problems, (f"6 processes with the CPU saturated, {6 * n} turns in {time.time() - t0:.1f}s: every "
                          f"message committed once, versions without gaps, 2 workers racing on one session; "
                          f"{late} late commits refused and redelivered, {waits} waits for a held lease" +
                          (f"; problems {problems}" if problems else ""))


def scenario_guard_down(db):
    txn = failed_txns()[0]
    c = container(db)
    sid = c.worker.start_session("m_100", ["support"], "customer")
    calls: list = []
    slow_create(c.chatbot.engine(), 0, calls)
    real = c.chatbot.guard.check_input

    def down(*a, **k):
        raise TimeoutError("guardrail endpoint timed out")
    c.chatbot.guard.check_input = down
    r1 = c.worker.handle(sid, "msg-guard-001", f"why did {txn} fail?")
    v1, n1 = version(db, sid), len(calls)
    c.chatbot.guard.check_input = real
    r2 = c.worker.handle(sid, "msg-guard-001", f"why did {txn} fail?")
    c.close()
    ok = r1["blocked_by"] == "unavailable" and n1 == 0 and v1 == 0 and "declined" in r2["answer"]
    return ok, f"guard down: {r1['blocked_by']}, model calls {n1}, nothing committed; resend later answered"


def scenario_ingestion(tmp):
    src = os.path.join(ROOT, "data", "kb_pages.json")
    dst = os.path.join(tmp, "kb_vectors.json")
    p = child("ingest", src, dst, "0.12", wait=False)
    time.sleep(1.2)
    p.send_signal(signal.SIGKILL)
    p.wait()
    with open(dst) as f:
        partial = len({r["doc_id"] for r in json.load(f)})          # valid JSON: atomic saves
    second = child("ingest", src, dst, "0")
    third = child("ingest", src, dst, "0")
    with open(dst) as f:
        recs = json.load(f)
    ids = [r["id"] for r in recs]
    docs = {r["doc_id"] for r in recs}
    ok = (0 < partial < 20 and second["indexed"] + second["skipped"] == 20 and second["skipped"] == partial
          and third["indexed"] == 0 and third["skipped"] == 20 and len(ids) == len(set(ids)) and len(docs) == 20
          and "209" not in docs)
    return ok, (f"killed after {partial}/20 docs; index file still valid; re-run indexed {second['indexed']} and "
                f"skipped {second['skipped']}; third run indexed {third['indexed']}; no duplicate chunks; "
                f"injection page quarantined")


SCENARIOS = {"crash": scenario_crash, "lost_reply": scenario_lost_reply, "zombie": scenario_zombie,
             "memory": scenario_memory, "failover": scenario_failover, "contention": scenario_contention,
             "guard_down": scenario_guard_down, "ingestion": scenario_ingestion}


def main(names: list[str]) -> int:
    failed = 0
    for name in names or list(SCENARIOS):
        with tempfile.TemporaryDirectory() as tmp:
            arg = tmp if name == "ingestion" else os.path.join(tmp, "sessions.db")
            t0 = time.time()
            try:
                ok, detail = SCENARIOS[name](arg)
            except Exception as e:             # noqa: BLE001 - report and keep going
                ok, detail = False, f"{type(e).__name__}: {e}"
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name:11s} {time.time() - t0:5.1f}s  {detail}", flush=True)
    print(f"\n{len(names or SCENARIOS) - failed}/{len(names or SCENARIOS)} scenarios passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        run_child(sys.argv[2], sys.argv[3:])
    else:
        sys.exit(main(sys.argv[1:]))
