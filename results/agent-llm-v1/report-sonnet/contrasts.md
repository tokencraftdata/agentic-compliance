# LLM-agent study: paired contrasts

240 episodes, 220 complete. 95% cluster-bootstrap intervals over market paths.

## claude-sonnet-5-5, buy_parent

| Contrast | Alert diff [95% CI] | Task endpoint diff [95% CI] | Pairs | Paths | Incomplete |
| --- | --- | --- | --- | --- | --- |
| aggregate_vs_off | 0.000 [0.000, 0.000] | 0.012 [0.000, 0.031] | 43 | 15 | 17 |
| reminder_vs_task_only | 0.000 [0.000, 0.000] | 0.013 [0.000, 0.034] | 43 | 15 | 17 |
| response_on_vs_off | 0.000 [0.000, 0.000] | -0.009 [-0.032, 0.005] | 45 | 15 | 15 |

## claude-sonnet-5-5, market_making

| Contrast | Alert diff [95% CI] | Task endpoint diff [95% CI] | Pairs | Paths | Incomplete |
| --- | --- | --- | --- | --- | --- |
| aggregate_vs_off | -0.193 [-0.309, -0.085] | -3.158 [-4.268, -1.931] | 57 | 15 | 3 |
| reminder_vs_task_only | -0.105 [-0.196, -0.017] | -0.877 [-2.153, 0.390] | 57 | 15 | 3 |
| response_on_vs_off | -0.053 [-0.125, 0.018] | -0.175 [-1.679, 1.190] | 57 | 15 | 3 |

