# Version history

The paper and code went through several review rounds. Earlier versions and their
evidence are kept in the repository so every published number can be traced back.

| Version | Date | What changed |
| --- | --- | --- |
| Paper v2.4 | 10 Nov 2026 | Renamed the simulator and workflow **TradeGauntlet** (previously ConductLab) and put it in the title. No change to experiments, results or references. |
| Paper v2.3 | Nov 2026 | Added 560 boundary episodes (`results/boundary-v1`), min/mean/max ranges, separated rejection causes, prose edit. See [review response](../paper/offline_study/REVIEW_RESPONSE_v2_3.md). |
| Paper v2.2 | Nov 2026 | Added five published financial-agent and compliance papers (29 references in total). Kept LLM results explicitly pending. |
| Paper v2.1 | Nov 2026 | Added the Robinhood trading-agent motivation and practical testing uses. |
| Paper v2.0 | Nov 2026 | Added 2,140 sensitivity episodes (`results/sensitivity-v1`), formal detector definitions and finance-journal references. Narrowed the aggregate-cap claim. See [major review response](../paper/offline_study/MAJOR_REVIEW_RESPONSE.md). |
| Agent study (phase 2) | Nov 2026 | Closed-loop LLM runner, held-out scenarios, spending ledger and replay in `agent_study/`. Only mock software checks have run. |
| Repository separation | 8 Nov 2026 | Split out of the TrustBound repository. See [provenance](../provenance/README.md). |
| Pilot v0.4 | Nov 2026 | Review revision of the scripted pilot: aggregate gate, larger market maker, sensitivity sweeps, mock experiment smoke grid. See [review revision](history/review-revision-v0.4.md). |
| Pilot v0.2 | 6 Nov 2026 | First offline pilot: 160 calibration and 960 evaluation episodes (`results/offline-v2`). See [revision notes](history/revision-notes-v0.2.md) and [protocol](pilot-protocol.md). |

The original *Accidental Spoofers* proposal, which framed the LLM experiment before
any code existed, is preserved as [the original design](history/original-design.md).
