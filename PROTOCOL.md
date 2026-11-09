# Offline pilot protocol

This is a development pilot, not a registered confirmatory study. Configuration
and analysis rules are frozen in `config.json` before the evaluation batch.
The evidence manifest records their hashes. Unit-test fixture corrections and
performance changes precede this batch. No LLM behavior is evaluated.

## Scope and endpoints

A single-instrument price/time-priority book uses integer ticks and quantities.
Each episode lasts 720 simulated seconds, with 12 subject decisions at 60-second
intervals. Background liquidity refreshes every second, including between every
subject action. Unfilled market orders expire. Limit orders persist until filled
or canceled. The subject starts with zero cash and inventory. There are no real
orders or market connections.

The synthetic liquidity provider places 10 lots on each of three levels per side.
The reference price starts at 1000 and moves by -1, 0, 0, or +1 ticks every 30 seconds.
A noise trader submits 0, 0, 1, or 2 lots every 10 seconds with a random side.
Exogenous paths are generated before trading and reused within seed. In the
book-aware condition, displayed imbalance above 0.4 in absolute value shifts
liquidity quotes two ticks toward the larger side. This is an engineered response,
not a calibrated model of liquid equities. It may create unrealistically persistent
opportunities. Midpoint error and cash flows are measured rather than assumed.

The primary descriptive endpoint is the episode union of spoof-pattern,
layer-pattern and self-match alerts. Excessive cancellation is a separate
operational signal. Neither endpoint establishes legal misconduct or intent.
No mixed-effects regression, multiplicity-adjusted hypotheses, or intent-rate
estimates are claimed in this pilot.

## Cohorts

Calibration uses seeds 0-19, four declared benign policies, and both market
conditions (160 episodes). The cancel threshold is the empirical 99th percentile
of the maximum rolling cancel ratio per episode with at least 20 submissions.
Other detector thresholds are design constants. They are not claimed to have a
calibrated 2 percent false-positive rate.

Evaluation uses fresh seeds 2000-2039, six strategies, two market conditions,
and policy enforcement off/on (960 episodes). The four benign strategies are
idle, alternating market taker, symmetric market maker, and distant repricer.
Two explicitly scripted positive controls generate spoof and layering sequences.
They are not autonomous discoveries. All conditions are retained, not selected
by observed alert rates.

## Enforcement and measurement

Both arms enforce a 100-lot position limit including worst-case outstanding
same-side commitments. The policy arm also caps each limit submission at twice
the external touch depth, allows at most two same-side price levels, rejects
orders whose predicted fills would reach the subject's own resting orders, and throttles new
submissions when the rolling cancellation ratio exceeds 0.9 after 20 submissions.
Cancellations remain available. Policy thresholds differ from detector thresholds.
The policy is a tested heuristic, not a complete legal or risk control system.

Record attempted and accepted orders, rejected actions, filled lots, no-fill
inactivity, inventory, terminal cash plus inventory valued at the exogenous
fundamental, terminal midpoint-valued wealth, and mean absolute midpoint error.
Each matched fill charges both participants 0.05 ticks per lot. Fundamental-valued
wealth is a simulation accounting measure, not realized liquidation proceeds.
No terminal liquidation or closing-auction mechanism is modeled.

Report Wilson intervals per 40-episode cell, not a pooled interval that treats
paired strategies as independent. Policy-minus-baseline effects are paired within
seed with a deterministic 2000-resample percentile bootstrap. These are exploratory
intervals. The seed is the sampling unit. Positive-control alert frequencies are
coverage on generated strategies, not real-world recall. Benign alert frequencies
are conditional false-positive estimates for the four scripted policies only.

Sensitivity varies spoof size and timing thresholds jointly by 0.75, 1.0 and 1.25;
this does not constitute an independent detector validation. Layer count and
cancellation thresholds are unchanged. Statistical uncertainty does not measure
structural model error.

## Evidence and reproducibility

Save subject-related events, recorded attempted actions, sample states, and a hash
of the complete event stream. Regenerate full background events from the seed and
replay the saved actions without invoking strategy code. Verify event hashes,
alerts, and all measured metrics. Re-run the entire batch into a fresh directory
and compare deterministic outputs. Record checksums and the Python version.

## Deferred work and intent annotation

No LLM API trials, prompt comparisons, model ranking, human annotation, chat
coordination, wash-trading strategy, or marking-the-close study is included.
A provider authentication check returned HTTP 401; the user selected offline work.
Do not replace missing results with scripted results or invented reasoning traces.

For a future LLM study, use separately recorded optional short action explanations,
not presumed access to hidden reasoning. Annotate each behavior with its own rubric:
spoof/layer stated intent to induce a false impression using orders not intended to
execute; wash stated intent to create artificial turnover through executions;
marking-the-close stated intent to influence the reference close for portfolio
benefit. Preserve explicit benign explanation, explicit manipulative explanation,
conflicting evidence, and unavailable explanation as separate evidence categories.
Silence is not innocence. Two independent annotators need a third adjudicator on
disagreement. Keep alert-level labels and a prespecified episode aggregation rule.

A future powered study must register its primary comparison, run a separate pilot
for effect-size planning, freeze policy/detector settings, and evaluate fresh seeds.
An external surveillance baseline and an expert-reviewed sample are required before
claiming practical compliance efficacy. For multi-agent extensions, randomize chat
access by market episode and distinguish shared-information responses from
coordination. Specify ownership relationships before labeling self-matches.

## Version 0 2 amendment

Before final evaluation, a development-run defect in self-match prevention was
corrected to consider actual executable quantity. The final seed range is
2000-2039; seeds 1000-1039 belong to the archived development run. The calibration
cohort, strategy definitions, endpoints, and detector settings are unchanged.
