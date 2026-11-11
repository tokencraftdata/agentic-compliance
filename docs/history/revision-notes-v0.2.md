# Revision record

The supplied manuscript proposed an LLM experiment with placeholder results. The
revised manuscript reports the implemented offline pilot and retains the LLM
question as future work. No numerical result from scripted strategies is labeled
as an LLM finding.

| Review issue | Resolution |
|---|---|
| Results stated before evidence | Replace every placeholder with measured pilot data or an explicit deferred-work statement. Remove claims about LLM emergence and coordination. |
| Overbroad novelty | Acknowledge prior continuous double auctions and ReguSim; describe order lifecycle and replay as this artifact's scope, not a proven first. |
| One intent rubric for different abuses | Specify behavior-specific future rubrics; no inferred intent rates in the pilot. Third adjudicator resolves two-rater disagreements. |
| Per-detector versus joint false positives | Report separate detectors and their episode union on held-out benign controls. No unsupported 2% guarantee. |
| Cancellation as abuse | Keep excessive cancellation separate from the primary conduct-pattern endpoint. |
| Inactivity mistaken for compliance | Report volume, inactivity, attempted/accepted orders and rejections alongside alerts and wealth. |
| Circular control evaluation | Use independent midpoint error and wealth diagnostics; disclose overlap between gate rules and pattern detectors. External validity remains unestablished. |
| Unsafe cancellation throttle | Throttle new submissions; allow cancellations. Include outstanding orders in exposure limits. |
| Frozen market during agent actions | Advance background by one second after every action; fixed exogenous streams preserve seed pairing. |
| Marked profit mistaken for realized profit | Report fundamental-valued and midpoint-valued wealth; explicitly state no terminal liquidation. |
| Selecting high-flag cells | Run and retain all fixed conditions on disjoint evaluation seeds. |
| Weak statistical precision | Report per-cell Wilson and paired descriptive bootstrap intervals; no confirmatory regression or p-values. |
| Invalid multi-agent proxies | Defer coordination study; prescribe episode-level randomization and evidence beyond synchrony. |
| Legal scope conflation | Distinguish pattern alerts from legal findings and CFTC spoofing provisions from SEC/FINRA obligations. |
| Incomplete bibliography | Retain a smaller verified bibliography; identify the market-abuse ethics paper as ICAIF 2025. |
| Unreproducible proof claims | Include tests, finite-domain checks, recorded actions, complete-event hashes and deterministic replay; make no unbounded proof claim. |

This is a substantive scope correction, not a successful completion of the
original 1,280-episode LLM study. A provider credential was present but rejected
by its API with HTTP 401. No model inference was completed. The user explicitly
authorized the offline scope. No credential was logged or committed.

No institutional ethics approval, expert validation, public preregistration,
real-market calibration, production certification or public release is claimed.

## Development correction before the final batch

The first offline batch used seeds 1000-1039. Review found that the self-match
gate rejected an order whenever it crossed any own quote, even when enough
better-priced external liquidity would fill it first. A regression test was
added and the gate now simulates the matching walk including quantity and
price/time priority. The first batch, original code, and successful full replay
and byte-comparison evidence are preserved in `results/development-v1.zip`.

The final v0.2 batch uses fresh seeds 2000-2039. Calibration seeds and detector
thresholds were not retuned. This remains a development pilot, not a preregistered
confirmatory experiment. The corrected gate is synchronous with this simulator;
real execution races and venue-specific prevention rules are outside its scope.
