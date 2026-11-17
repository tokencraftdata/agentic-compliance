"""Canary rollout with automatic rollback (finbot/rollout.py decides; this applies it).

Two Deployments per role: `finbot-worker` (stable) and `finbot-worker-canary`. Traffic share is
set by replica counts: the queue spreads turns over all pods, so 2 canary pods of 20 get ~10%.

    python3 scripts/rollout.py --image REGISTRY/finbot@sha256:... --steps 10,50,100 --bake-minutes 15 \\
        --metrics-cmd "python scripts/canary_metrics.py"

At each step: scale the canary, bake, read metrics (a command printing
{"canary": {...}, "baseline": {...}} with the fields of finbot.rollout.Metrics), then decide.
Promote → next step; wait → bake again (at most --max-waits); rollback → canary to 0, stable
untouched. After 100%: the stable Deployment takes the new image and the canary goes to 0.
"""
import argparse
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from finbot.rollout import Metrics, decide  # noqa: E402

NS, STABLE, CANARY = "finbot", "finbot-worker", "finbot-worker-canary"


def kubectl(*args: str) -> str:
    exe = shutil.which("kubectl") or "kubectl"
    return subprocess.run([exe, "-n", NS, *args], check=True, capture_output=True, text=True).stdout  # noqa: S603


def replicas(name: str, run=kubectl) -> int:
    return int(run("get", "deployment", name, "-o", "jsonpath={.spec.replicas}") or 0)


def scale(name: str, n: int, run=kubectl) -> None:
    run("scale", "deployment", name, f"--replicas={n}")


def rollout(image: str, steps: list[int], bake_s: float, metrics, run=kubectl, sleep=time.sleep,
            max_waits: int = 3, log=print) -> str:
    total = replicas(STABLE, run) + replicas(CANARY, run)
    run("set", "image", f"deployment/{CANARY}", f"worker={image}")
    for pct in steps:
        n = min(total, max(1, math.ceil(total * pct / 100)))
        scale(CANARY, n, run)
        scale(STABLE, total - n, run)
        for _ in range(max_waits + 1):
            sleep(bake_s)
            m = metrics()
            d = decide(Metrics(**m["canary"]), Metrics(**m["baseline"]))
            log(f"step {pct}%: {d.action} {' '.join(d.reasons)}")
            if d.action != "wait":
                break
        if d.action != "promote":
            scale(CANARY, 0, run)
            scale(STABLE, total, run)
            log(f"rolled back at {pct}%: {', '.join(d.reasons) or 'not enough data'}")
            return "rolled_back"
    run("set", "image", f"deployment/{STABLE}", f"worker={image}")
    scale(STABLE, total, run)
    scale(CANARY, 0, run)
    log("promoted to 100%")
    return "promoted"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--steps", default="10,50,100")
    ap.add_argument("--bake-minutes", type=float, default=15)
    ap.add_argument("--metrics-cmd", default=os.environ.get("FINBOT_CANARY_METRICS_CMD", ""))
    a = ap.parse_args()
    if not a.metrics_cmd:
        print("--metrics-cmd is required: a command that prints canary and baseline metrics as JSON")
        return 2

    def metrics():
        out = subprocess.run(shlex.split(a.metrics_cmd), check=True, capture_output=True, text=True).stdout  # noqa: S603
        return json.loads(out)
    result = rollout(a.image, [int(x) for x in a.steps.split(",")], a.bake_minutes * 60, metrics)
    return 0 if result == "promoted" else 1


if __name__ == "__main__":
    sys.exit(main())
