# Review revision 0.4

The supplied `pilot.pdf` is the eight-page technical manuscript. `main_1.pdf` is
byte-identical to the original ten-page full-paper proposal. They are kept as
separate works: the corrected pilot is intended for later reuse as Appendix B,
while all LLM findings in the full paper remain pending.

The original protocol 0.2 code and results are unchanged. The old Word/PDF and
`RELEASE_MANIFEST.json` describe that historical release, not revision 0.4.

## Review findings resolved

| Finding | Revision |
|---|---|
| Split-order blind spot | Added per-owner, per-side aggregate gate; added related aggregate detector; retain and publish the original failure |
| Layerer explanation | Corrected: two levels accepted, third rejected by legacy gate |
| Overclaiming control efficacy | Report mechanism and known-pattern repair only; show residual detector/gate dependence |
| Missing trading-cost controls | Added larger legitimate market-maker script and report volume, wealth, rejects and inactivity |
| Weak sensitivity analysis | Added independent one-at-a-time parameter sweeps; label them post-hoc |
| Unsupported preregistration | Changed to proposed future study; registration and final power planning remain pending |
| Inconsistent future design | Explicit gate dimension; compatible 80-lot parent and 60-second schedule; ownership and close semantics stated as prerequisites |
| Regulatory premise | Removed claim that rules presume humans or fixed algorithms |
| Misleading figure | Separate axes for lots, wealth and price error |
| Version ambiguity | Manuscript revision 0.4, historical experiment 0.2 and exploratory extension 0.4 distinguished |
| Missing full-study infrastructure | Added provider interface, closed-loop runner, strict action validation, failure bounds and mock smoke/replay checks |
| Unvalidated market opportunity | Added a sourced commercial assessment, competition analysis and concrete customer-validation plan; no demand claim |

## Evidence cohorts

- Original calibration: 160 episodes, seeds 0-19.
- Original evaluation: 960 episodes, seeds 2000-2039, protocol 0.2.
- Post-hoc discovery probe: 160 episodes reusing seeds 2000-2039; separate evidence.
- Exploratory repair evaluation: 2,160 episodes, seeds 3000-3039; 9 strategies x
  2 market responses x 3 gates x 40 seeds. Fresh paths, not independent strategies.
- Experiment plumbing: 32 offline mock episodes, seeds 4000-4001; zero LLM episodes.

The wider maker can move the midpoint without a manipulative objective. Therefore
price error alone is not a manipulation or welfare-loss label. The repaired gate
is not a universal defense, and the added detector is not an independent family.

## Reproduce

```sh
python3 -m unittest discover -s tests -v
python3 check_invariants.py
python3 replay.py results/offline-v2
python3 -m pilot.review_study verify results/review-v0.4
python3 -m pilot.review_study run results/local-review
python3 -m pilot.review_study compare results/review-v0.4 results/local-review
python3 review_sensitivity.py results/local-review results/local-sensitivity.json
python3 -m experiments.runner verify results/experiment-smoke-v0.4
```

The discovery script in `results/review-checks` takes the project path as its
argument. Its copied output path follows the script location; preserve the saved
evidence and rerun a copy in a temporary directory.

No live models or customers have been contacted. The commercial plan and full-paper
design identify the remaining external validation steps explicitly.
