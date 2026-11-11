# TradeGauntlet

TradeGauntlet is the name for this project's custom limit-order-book simulator and
workflow for evaluating trading controls. It was built for the compliance study;
it does not wrap ABIDES or connect to an exchange. The repository name remains
`agentic-compliance`.

The workflow fixes a scenario, runs matched control conditions, separates alerts
from response-dependent wealth and execution cost, and replays the recorded
attempts. It is intended for developing and testing controls. It is not a
calibrated market model, a general-purpose exchange simulator, or a legal
compliance certification.

| Step | Implementation | Saved evidence |
| --- | --- | --- |
| Define the market and actions | `pilot/market.py`, `pilot/strategies.py`, `pilot/revision.py` | Frozen cohort configurations |
| Fix a comparison before running it | `paper_study/sensitivity.py`, `paper_study/boundary.py` | Plan, parameters, seeds, source hashes |
| Run paired paths with different gates or responses | The same scenario runners | Attempted actions, accepted events, rejection reasons, metrics |
| Score and compare | `pilot/detectors.py`, `pilot/revision.py`, `paper_study/reporting.py` | Alerts, paired wealth differences, volume, observed ranges |
| Replay and check | Each runner's `verify` command | Full event hashes, record equality, recomputed summaries |

The custom engine supports integer ticks, price/time priority, partial fills,
cancellations, position accounting, fees and deterministic background paths.
The sensitivity runner exposes aggregate cap multiples, response thresholds,
throttle settings and session lengths. The boundary runner additionally tests
exact quoting cadences that are not multiples of the usual decision interval.
The saved study plans specify the tested settings; these are research scripts,
not a packaged configuration API for arbitrary trading systems.

From the repository root, with CPython 3.9.6:

```sh
python3 -m paper_study.boundary plan results/my-boundary-check
python3 -m paper_study.boundary run results/my-boundary-check
python3 -m paper_study.boundary verify results/my-boundary-check
```

Use a new directory for each experiment. To change a scenario or control,
version the runner and freeze another plan before execution. Preserve the old
source and saved evidence when comparing versions. A successful replay checks
the implementation against its recorded execution; it does not independently
validate the market assumptions or the script's assigned intent.

The current checks use one subject account, one instrument, synthetic prices
and fixed trading scripts. The actual-LLM runner is under `agent_study/`, but
only mock software checks have run. Actual LLM findings and independently
authored test scenarios remain pending. The paper's source and detailed
reproduction instructions are in [paper/offline_study](paper/offline_study/README.md).
