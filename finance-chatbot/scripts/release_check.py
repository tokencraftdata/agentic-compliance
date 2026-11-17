"""Release gate: every check a release candidate must pass, with fixed thresholds.

    python3 scripts/release_check.py                 # all gates; exit 1 if any fails
    python3 scripts/release_check.py --quick         # skip the slow gates (durability, stress)
    python3 scripts/release_check.py --report release-report.md

A gate is a command plus a rule for its output. Thresholds are absolute, not "no worse than
last time": a release that lets one injected hallucination or one attack through does not ship.

    version       pyproject.toml, finbot.__version__ and the top CHANGELOG entry agree
    tree          no uncommitted changes (so the report describes the commit being released)
    lint, types   ruff and mypy clean
    tests         the full unit and integration suite passes
    eval          every case on the dev and held-out sets passes
    stress        0 injected hallucinations reach the user, on both data sets
    false_blocks  0 correct answers blocked, on both data sets
    consistency   pass^16 = 1.000
    redteam       0 attacks succeed against the defended configuration; same for multi-turn
    kb_audit      0 findings in the help pages
    durability    every multi-process scenario passes (kill -9, frozen worker, failover, ...)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = sys.executable


@dataclass
class Gate:
    name: str
    cmd: list[str]
    passed: Callable[[int, str], tuple[bool, str]]
    slow: bool = False


def _exit_zero(code: int, out: str) -> tuple[bool, str]:
    tail = out.strip().splitlines()[-1] if out.strip() else ""
    return code == 0, tail[:160]


def _all_passed(code: int, out: str) -> tuple[bool, str]:
    m = re.search(r"(\d+)/(\d+) passed", out)
    return bool(m and m.group(1) == m.group(2) and code == 0), m.group(0) if m else "no result line"


def _leaked_zero(code: int, out: str) -> tuple[bool, str]:
    rows = [ln.split() for ln in out.splitlines()]
    start = next((i for i, r in enumerate(rows) if r[:1] == ["mode"]), None)
    if start is None:
        return False, "no stress table"
    injected = leaked = 0
    for r in rows[start + 1:]:
        if len(r) >= 5 and r[1].isdigit():
            injected, leaked = injected + int(r[1]), leaked + int(r[4])
    return code == 0 and leaked == 0 and injected > 0, f"{leaked} of {injected} leaked"


def _blocked_zero(code: int, out: str) -> tuple[bool, str]:
    m = re.search(r"TOTAL (\d+) asked (\d+) blocked", out)
    return bool(m and m.group(2) == "0" and code == 0), m.group(0) if m else "no total"


def _defended_zero(code: int, out: str) -> tuple[bool, str]:
    m = re.search(r"^defended\s+(\d+)/(\d+)", out, re.M)
    return bool(m and m.group(1) == "0"), f"defended {m.group(1)}/{m.group(2)}" if m else "no defended row"


def _pass_k(code: int, out: str) -> tuple[bool, str]:
    found = re.findall(r"pass\^(\d+) = ([\d.]+)", out)
    if not found:
        return False, "no pass^k"
    k, value = max(found, key=lambda kv: int(kv[0]))      # the largest k is the claim that matters
    return float(value) == 1.0 and code == 0, f"pass^{k} = {value}"


def _scenarios(code: int, out: str) -> tuple[bool, str]:
    m = re.search(r"(\d+)/(\d+) scenarios passed", out)
    return bool(m and m.group(1) == m.group(2) and code == 0), m.group(0) if m else "no result"


def _version() -> tuple[bool, str]:
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        project = tomllib.load(f)["project"]["version"]
    sys.path.insert(0, ROOT)
    import finbot
    with open(os.path.join(ROOT, "CHANGELOG.md")) as f:
        top = next((ln for ln in f if ln.startswith("## [")), "")
    ok = project == finbot.__version__ and f"[{project}]" in top
    return ok, f"pyproject {project}, package {finbot.__version__}, changelog {top.strip()[3:40]}"


def _tree() -> tuple[bool, str]:
    git = shutil.which("git") or "git"
    r = subprocess.run([git, "status", "--porcelain", "--", "."], cwd=ROOT, capture_output=True, text=True)  # noqa: S603
    dirty = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return not dirty, "clean" if not dirty else f"{len(dirty)} uncommitted file(s)"


def gates() -> list[Gate]:
    s = os.path.join(ROOT, "scripts")
    return [
        Gate("lint", [PY, "-m", "ruff", "check", "."], _exit_zero),
        Gate("types", [PY, "-m", "mypy", "finbot"], _exit_zero),
        Gate("tests", [PY, "-m", "unittest", "discover", "-s", "tests", "-q"], _exit_zero),
        Gate("eval_dev", [PY, f"{s}/run_eval.py", "--data", "data"], _all_passed),
        Gate("eval_heldout", [PY, f"{s}/run_eval.py", "--data", "data/test"], _all_passed),
        Gate("stress_dev", [PY, f"{s}/run_eval.py", "--hallucinate", "--data", "data"], _leaked_zero, slow=True),
        Gate("stress_heldout", [PY, f"{s}/run_eval.py", "--hallucinate", "--data", "data/test"], _leaked_zero,
             slow=True),
        Gate("false_blocks", [PY, f"{s}/false_block_probe.py", "data", "data/test"], _blocked_zero, slow=True),
        Gate("consistency", [PY, f"{s}/run_consistency.py", "--k", "16"], _pass_k),
        Gate("redteam", [PY, f"{s}/run_redteam.py"], _defended_zero),
        Gate("redteam_multiturn", [PY, f"{s}/run_redteam.py", "--cases", "data/redteam_multiturn.jsonl"],
             _defended_zero),
        Gate("kb_audit", [PY, f"{s}/kb_audit.py"], _exit_zero),
        Gate("durability", [PY, f"{s}/durability_test.py"], _scenarios, slow=True),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip slow gates (stress, false blocks, durability)")
    ap.add_argument("--allow-dirty", action="store_true", help="do not require a clean working tree")
    ap.add_argument("--report", default="", help="write a markdown report here")
    a = ap.parse_args()
    rows: list[tuple[str, bool, str, float]] = []
    for name, fn in (("version", _version), ("tree", _tree)):
        if name == "tree" and a.allow_dirty:
            continue
        ok, detail = fn()
        rows.append((name, ok, detail, 0.0))
        print(f"{'PASS' if ok else 'FAIL'}  {name:18s} {detail}", flush=True)
    for g in gates():
        if g.slow and a.quick:
            continue
        t0 = time.time()
        r = subprocess.run(g.cmd, cwd=ROOT, capture_output=True, text=True)  # noqa: S603 - fixed commands
        ok, detail = g.passed(r.returncode, r.stdout + r.stderr)
        rows.append((g.name, ok, detail, time.time() - t0))
        print(f"{'PASS' if ok else 'FAIL'}  {g.name:18s} {detail}  ({time.time() - t0:.0f}s)", flush=True)
    failed = [n for n, ok, _, _ in rows if not ok]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} gates passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    if a.report:
        with open(a.report, "w") as f:
            f.write("# Release gate report\n\n| Gate | Result | Detail | Seconds |\n|---|---|---|---|\n")
            for n, ok, d, sec in rows:
                f.write(f"| {n} | {'pass' if ok else '**FAIL**'} | {d} | {sec:.0f} |\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
