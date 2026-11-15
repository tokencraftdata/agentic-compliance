"""Run processor-orchestration scenarios through simulated processors.

  python3 scripts/simulate_routing.py                          # every scenario, 2,000 payments each
  python3 scripts/simulate_routing.py -s northstar_outage -n 5000
  python3 scripts/simulate_routing.py --json                   # machine-readable

Each scenario is one simulated day pushed through the real router, cascade policy
and circuit breakers (finbot/processors). Same seed, same numbers.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from finbot.processors.simulator import SCENARIOS, run_scenario  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-s", "--scenario", choices=sorted(SCENARIOS), action="append")
    ap.add_argument("-n", "--payments", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    reports = [run_scenario(SCENARIOS[n], a.payments, a.seed).as_dict() for n in (a.scenario or SCENARIOS)]
    if a.json:
        print(json.dumps(reports, indent=1))
        return
    print(f"{'scenario':<18}{'approval':>9}{'cascaded':>10}{'att/pmt':>9}{'unknown':>9}{'no route':>9}   "
          "per processor: attempts / processor errors")
    for r in reports:
        per = "  ".join(f"{p}:{v['attempts']}/{v['processor_errors']}" for p, v in sorted(r["by_processor"].items()))
        print(f"{r['scenario']:<18}{r['approval_rate']:>9.1%}{r['cascaded']:>10}{r['attempts_per_payment']:>9}"
              f"{r['unknown_outcome']:>9}{r['not_routed']:>9}   {per}")


if __name__ == "__main__":
    main()
