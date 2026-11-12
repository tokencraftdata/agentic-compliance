# LLM-agent study: paired contrasts

1440 episodes, 1342 complete. 95% cluster-bootstrap intervals over market paths.

## claude-haiku-5-5, buy_parent

| Contrast | Alert diff [95% CI] | Task endpoint diff [95% CI] | Pairs | Paths | Incomplete |
| --- | --- | --- | --- | --- | --- |
| aggregate_vs_off | 0.000 [0.000, 0.000] | -0.054 [-0.101, -0.005] | 207 | 30 | 33 |
| legacy_vs_off | 0.000 [0.000, 0.000] | -0.011 [-0.056, 0.037] | 208 | 30 | 32 |
| aggregate_vs_legacy | 0.000 [0.000, 0.000] | -0.042 [-0.089, 0.010] | 213 | 30 | 27 |
| reminder_vs_task_only | 0.000 [0.000, 0.000] | 0.034 [-0.012, 0.087] | 314 | 30 | 46 |
| response_on_vs_off | 0.000 [0.000, 0.000] | -0.030 [-0.060, -0.001] | 313 | 30 | 47 |

## claude-haiku-5-5, market_making

| Contrast | Alert diff [95% CI] | Task endpoint diff [95% CI] | Pairs | Paths | Incomplete |
| --- | --- | --- | --- | --- | --- |
| aggregate_vs_off | -0.014 [-0.030, 0.000] | 0.289 [-0.535, 1.057] | 211 | 30 | 29 |
| legacy_vs_off | -0.014 [-0.033, 0.000] | -0.348 [-1.289, 0.540] | 210 | 30 | 30 |
| aggregate_vs_legacy | 0.000 [0.000, 0.000] | 0.714 [-0.095, 1.460] | 210 | 30 | 30 |
| reminder_vs_task_only | 0.000 [-0.009, 0.010] | -2.797 [-3.707, -1.964] | 311 | 30 | 49 |
| response_on_vs_off | -0.003 [-0.013, 0.006] | 0.360 [-0.231, 1.020] | 317 | 30 | 43 |

