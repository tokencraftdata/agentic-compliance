# Published research for the trading-agent phase

Checked 10 November 2026 against publisher, conference and author sources.
These are published conference or journal records, not merely arXiv uploads.
This is a targeted review, not an exhaustive systematic search. Where only a
publisher abstract was accessible, that limitation is stated below.

## Closest work and implications

1. **Avinash Kumar Pandey and Swati Rajwal. Evaluating the Ethical Judgment of
   Large Language Models in Financial Market Abuse Cases.** ACM ICAIF 2025,
   pp. 132–140. [Published article](https://doi.org/10.1145/3768292.3770439).
   Tests ten LLMs on six judgment tasks using 1,971 variants of 73 enforcement
   narratives. It is direct prior work on LLM market-conduct assessment. Its
   text-based decisions differ from orders executed through a responsive book.
   Our proposed extension should measure behavior after control interventions,
   not claim the first LLM market-abuse evaluation. Publisher text inspected.

2. **Wenliang Huang, Junkai Li, Zengyi Yu and Xiangjie Kong. RISCTrade: A Dynamic
   Multi-Market Benchmark for Risk-Mandate Compliance of Large Language Models
   in Closed-Loop Trading.** ICIC 2026, LNCS 16648, pp. 360–370.
   [Published chapter](https://doi.org/10.1007/978-981-92-3400-4_30).
   Evaluates alignment with risk profiles in trading across three markets.
   This prevents a credible “first closed-loop LLM compliance benchmark” claim.
   Our intended distinction is order-level market conduct and control costs,
   rather than portfolio risk mandates. Publisher abstract and metadata inspected;
   full chapter not reviewed. First online 14 July 2026; Springer's canonical
   citation uses **2027** for the proceedings volume. Preserve that distinction
   rather than treating it as a future unpublished preprint.

3. **Haohang Li et al. INVESTORBENCH: A Benchmark for Financial Decision-Making
   Tasks with LLM-based Agent.** ACL 2025, Volume 1: Long Papers, pp. 2509–2525.
   [Published paper](https://aclanthology.org/2025.acl-long.126/).
   A multi-asset financial-agent benchmark comparing thirteen LLMs. It provides
   context for task and model comparisons; trading performance does not itself
   validate market-conduct detectors. ACL publication record and abstract checked.

4. **Wentao Zhang et al. A Multimodal Foundation Agent for Financial Trading:
   Tool-Augmented, Diversified, and Generalist (FinAgent).** ACM KDD 2024.
   [DOI](https://doi.org/10.1145/3637528.3671801),
   [author-hosted published PDF](https://personal.ntu.edu.sg/boan/papers/KDD24_FinAgent.pdf).
   Uses multimodal information, tools and memory for financial trading. Its
   published agent architecture is relevant to baseline design, but a richer
   architecture would require comparable information and tool budgets here.
   Publication verified from the proceedings citation and conference roster.

5. **Haochen Luo et al. QFinZero: A Unified Financial Toolchain for LLM-Based
   Trading Agents.** ACL 2026, Volume 3: System Demonstrations, pp. 68–77.
   [Published paper](https://aclanthology.org/2026.acl-demo.7/).
   Standardizes financial tools, stateful brokerage, order lifecycles and replay.
   Logging and reproducibility alone are therefore insufficient novelty claims.
   This is a system-demonstration paper, not an ACL main long paper. ACL record
   and abstract checked; its implementation has not been reproduced here.

## Journal evidence for the market mechanism

6. **Xuan Tao, Andrew Day, Lan Ling and Samuel Drapeau. On detecting spoofing
   strategies in high-frequency trading.** Quantitative Finance 22(8), 2022,
   pp. 1405–1425.
   [Published article](https://doi.org/10.1080/14697688.2022.2059390).
   Models spoofing and applies the analysis to TMX Level 2 data. This is a useful
   calibration reference; our synthetic book is not an empirical replication.
   Publisher record and abstract checked. Already cited in the current paper.

7. **Jianqiang Chen, Pei-Fang Hsieh and J. Jimmy Yang. Order spoofing, price impact,
   and market quality.** Pacific-Basin Finance Journal 97, 2026, article 103077.
   [Published article](https://doi.org/10.1016/j.pacfin.2026.103077).
   Studies potential spoofing in Taiwan derivatives data and an order-placement
   restriction. Its empirical setting supports examining economic outcomes along
   with order patterns, while also illustrating identification limits. Our
   simulator does not establish comparable real-market effects. Publisher
   abstract and accessible text inspected. Already cited in the current paper.

## What should change in the research claim

The defensible next question is: **Under legitimate execution and market-making
tasks, how do order controls alter an LLM agent's attempted actions, executions,
pattern alerts and task costs across held-out market conditions?**

That comparison can complement published risk-mandate and narrative-judgment
benchmarks. It becomes evidence only after real model calls and a completed
evaluation. A small single-provider validation run cannot support a general claim
about LLM traders, regulatory compliance or detector accuracy. External scenario
selection and independent behavior review would materially strengthen it.

Use the published venues and DOIs above when revising the paper's related work.
Keep workshop, main-conference, journal and preprint status distinct. Do not
silently upgrade a preprint based on a similarly titled paper. Manuscript version
2.2 integrates items 1-5 above as references 25-29; items 6-7 were already cited
as references 19-20. The paper clarifies its current contribution and describes
the actual-agent evaluation as pending. No LLM results have been added.
