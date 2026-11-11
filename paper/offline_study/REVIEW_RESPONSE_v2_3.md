# Response to the November 10 scientific review

The seven findings were checked against the implementation and saved records.
This revision retains the exploratory scope and adds 560 scripted episodes.

1. **Boundary equality.** Explicitly state the strict response inequality and
   inclusive detector age limit. At caps 2/4/6, test thresholds 0.24/0.26,
   0.39/0.41 and 0.49/0.51. Every lower-threshold split cell has D=48 on all ten
   seeds and every upper cell has D=0. At W=180, 170-second makers alert in 39/40
   cases and 190-second makers in 0/40. Reused seeds and rule dependence remain
   explicit. The new scheduler matches all 40 original 180-second event hashes.
2. **Execution cost.** Table 5 is split into volume and rejection panels, with
   cap/throttle/position/other means and observed ranges. Large-maker mean
   rejects reconcile as 20.30 + 7.40 + 0 + 0.25 = 27.95. The text distinguishes
   the separate matched throttle ablation from these counts. Patient-maker
   volume falls 28.2% with no throttle rejection; do not attribute both maker
   costs to the throttle. Counts record the first failed check, not all reasons
   an action would fail or a causal decomposition of volume.
3. **Cohorts.** Name modules and seeds beside every main comparison. Add ungated
   60.50- and 300.60-lot baseline rows to Table 4. Tables 2-5 report mean and range.
4. **Scoring window.** State 180 seconds as the current default; Table 2 retains
   the original 120-second configuration. Rescoring its 120 episodes at 180
   preserves all alert flags. Tuning reuse remains disclosed.
5. **Gate-detector inequality.** Explain why a fixed cap m<3 precludes the
   aggregate signal when all contributing orders passed that gate and their
   quantities cannot increase after admission. This uses the latest order's
   admission bound and the detector's maximum submission-time reference depth;
   it is not a global static-depth bound on book imbalance.
6. **Spread.** Tables now give observed minimum and maximum alongside means.
   In Table 3, D at t=0.2 ranges from -5.25 to 50.80 ungated and -5.85 to 65.90
   gated. These are descriptive draw-seed ranges, not confidence intervals or
   independent strategy replications. `results/reporting-v2_3.json` includes
   cell-level ranges and SDs for the underlying measurements.
7. **Artifact access.** The paper continues to state that the repository is
   private and does not claim public availability. The author has said they
   will make it public before submission. Visibility was not changed here.

Smaller fixes: remove reviewer-reply language from the narrative, state the
100% volume loss at cap 1, identify the ungated heatmap baselines, disconnect the
throttle-off point from the finite-window lines, clarify Robinhood's announced
status, check all four remaining arXiv citations, and retain pending LLM results
in the introduction and limitations rather than advertising them in the abstract.

**Reusable workflow.** The engine is custom-built. ConductLab names the existing
simulator, scenario runners, matched comparisons and replay checks. The paper
and root guide describe what can be reused without claiming a calibrated or
production-ready platform. This is a naming and documentation change, not a
new simulator implementation or a claim of methodological priority.
