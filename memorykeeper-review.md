# Review: MemoryKeeper — Durable Context Correction for Financial Agents

Reviewed: `tokencraftdata/fraud-memory-correction-research` at HEAD `8e9f88c9` (13 Nov 2026)
Scope: `paper/memorykeeper/manuscript.txt`, `heldout_study_v2/PROTOCOL.md`, `memorykeeper/guard.py`, `docs/bank_chatbot_integration.md`, top-level README.

## Summary

The paper asks whether an LLM agent keeps authorized corrections (a cleared finding stays cleared) across summaries and handoffs, without dropping independent concerns or accepting corrections that belong to another case. It compares four workflows on 6 synthetic templates: summary-only, ledger-only, ledger plus selective repair, and full rereading. Results: summary-only brings back withdrawn findings at 33/72 checkpoints. Both ledger arms have 0 revivals but accept wrong-case corrections 6 times. Selective repair **never triggered**. Full rereading was most accurate (95/96) and the cheapest. A deterministic guard added afterwards and replayed on the saved outputs withholds all 59 wrong outputs. The v2 held-out protocol (frozen) scales up to 24 cases, 3 models, 8 workflows and a real recipient boundary.

**Overall:** the process is rigorous and honest, but the method contribution is weak. As written this is a careful diagnostic note, not a methods paper. Recommendation: **reframe before submitting**, then aim for a workshop or a Findings/benchmark track (see "Path to publication").

## Strengths

1. **Splitting retention from authorization is the real contribution.** "Durable memory ≠ correct memory" is clean, easy to explain and useful in practice. The wrong-case acceptance finding (the ledger kept everything, then believed an update from `SYNTH-OTHER-CASE`) is the most interesting result in the paper.
2. **Unusually good research hygiene.** Protocols frozen and pushed before runs, gold kept away from the runner, explicit denominators, negative results reported, an abandoned run kept with its reason, and offline verification scripts that recompute every number.
3. **Action accuracy vs. state accuracy.** Summary-only scores 73/96 on actions but only 45/96 on exact handoffs, which shows clearly that "right answer" can hide a corrupted state.
4. **Resource accounting is reported**, including the inconvenient fact that rereading wins.

## Major concerns

### M1. The headline mechanism has no measured effect
The title and system name present MemoryKeeper as a correction method. But selective repair activated 0/96 times in finance, and in the earlier customer-service pilot it activated but changed no decision (192/192 vs 192/192, at +11.6% tokens). The 92 vs 94 ledger-arm gap is generation noise, which the paper admits. A reader will ask what MemoryKeeper actually contributes. Right now the honest answer is a benchmark plus a post hoc checker.

### M2. The guard result is true by construction
The guard and the gold labels use the same authored closed-form policy, and the replay hands every workflow the complete structured ledger packet. Getting 325 allowed and 0 incorrectly allowed is expected, not evidence. Table 4 sits next to the empirical tables and looks like a result. Move it to a "system" or "contract" section, or drop the table and keep one sentence.

### M3. A rule-based reducer solves the task
The typed reducer recovers 96/96 gold states, and the earlier rule oracle recovers 84/84. So the benchmark mostly measures whether an LLM can do bookkeeping that 50 lines of code do perfectly. The practical lesson, "don't let the LLM hold state; compute it", is valid, but it undercuts the memory-method framing. v2 takes a step toward the hard part by removing target IDs and using natural-language scope. That is the right direction and should become the paper's centre.

### M4. v2 statistical gate: ties may make it unpassable, so check before running
With 8 families there are 2^8 = 256 sign-flip permutations, so the smallest possible two-sided p is 2/256 ≈ 0.0078. Holm over 6 contrasts requires p ≤ 0.0083 for the first contrast and ≤ 0.010 and ≤ 0.0125 for the next two. Only p = 0.0078 meets any of these three, and that requires **every family to favour MemoryKeeper with no zero differences**: each zero-difference family doubles the minimum p (one tie gives 0.0156 or more). The gate needs every comparator to pass, typed state is expected to be near ceiling, and ties with it are likely. So the stronger-claim gate may fail for arithmetic reasons, not because the method lacks an effect. If no held-out calls have run yet, document an amendment, for example a different test statistic or treating ties explicitly. Otherwise, write this down in advance as a known limitation so a failed gate is not read as "no effect".

### M5. Statistical precision is very low
v1 has 6 clusters, so the bootstrap interval collapses to [0, 0]. v2 has 8 structural families from one author's generator. Even a clean v2 pass supports only "on this generator". State that plainly in the abstract rather than in hedges spread through the text.

### M6. One model provider
All three aliases (`gpt-6-luna/sol/astra`) come from one provider, called through a CLI with an unknown snapshot, temperature and seed. A memory/authorization paper in 2026 needs at least two more model families, such as Claude and an open-weights model. Wrong-case acceptance may be very model-specific.

### M7. Authority is assumed, not tested
Correctness depends on supplied `verified`, `case_id` and `revision` fields, and the guard depends on a "complete trusted snapshot". In production, establishing that trusted, complete, case-scoped snapshot is most of the problem. The invalid-authority family uses one fixed malicious instruction and nested metadata that disagrees with the outer record. That is a reasonable start, but add forged nested metadata, paraphrased authority claims inside document bodies, and cross-tenant references.

### M8. Regime choice favours rereading
With one decision per update, rereading is cheaper and better. Persistent memory pays off when there are many reads per update, as in long chats and many handoffs. Without a reads-per-update sweep the practical recommendation is "reread plus deterministic state", and the paper should say so directly instead of leaving it implied.

### M9. Little novelty left
BELIEFMEM, StateGraph, MemShift and especially *When Evidence Changes* (Chu 2026: memory repair vs. rereading, with costs) cover most of the ground. Section 2 is candid about this. That leaves the authorization angle and the evaluation discipline as the defensible novelty, so build the paper around them.

## Minor issues

- **Too many hedges.** Almost every paragraph carries a disclaimer ("not peer reviewed", "not equivalence", "not production"), which makes the text tiring and hides the findings. Gather them in one Limitations section and write the results in plain declarative sentences.
- **Repo naming and structure.** The repo is called `fraud-memory-correction-research` but leads with finance context memory and also holds three earlier studies. Split it, or put a two-line map at the top of the README. The README currently opens with an abandoned run.
- **"Payment services are excluded"**, yet payments are where these chatbots are deployed most (failed transactions, chargebacks, surcharges). Explain why, or add a payments domain in v3.
- Table 2 headers are broken in the text export ("Action\n/96 | Handoff\n/96"). Check the PDF.
- Write `h1…h4` and `R1/R2/R3` as a small timeline figure. The schedule in §4.1 is hard to follow in prose.
- Reference [22] (Travelers, Feb 2026) and the arXiv IDs 2610.x and 2611.x should be checked for exact titles and IDs before submission.
- Report cost in dollars at list price next to tokens. Reviewers want one number.

## Path to publication

1. **Reframe:** "Retention is not authorization: diagnosing correction failures in financial-agent memory". Make the benchmark plus the wrong-case finding the contribution, with selective repair as a reported negative result.
2. **Make the task hard enough that rules can't solve it:** natural-language scope from realistic documents, paraphrased and forged authority, and cross-tenant references. Report the deterministic reducer as the bar every LLM method must beat.
3. **Use 3 model families** with ≥3 replicates.
4. **Sweep reads per update** (1, 5, 20) so the cost crossover between memory and rereading is measured, not argued.
5. **Fix or pre-register the gate-tie issue (M4)** before v2 runs.
6. **Venues:** an agent-memory or LLM-safety workshop at NeurIPS/ICLR/ACL, or a benchmark/Findings track. A main-track methods paper is not realistic without a mechanism that shows an effect.

## How this maps to the chatbot

The paper's results translate directly into design rules for the finance chatbot (`finance-chatbot/`):

| Paper finding | Chatbot rule |
|---|---|
| Summary-only revives stale facts (33/72) | Don't carry transaction facts in chat history or summaries. Keep a typed per-entity ledger and re-render it each turn. |
| Ledger arms accept wrong-case updates | The ledger alone isn't enough. A deterministic **scope guard** checks that every tool result belongs to the session's tenant and the transaction in focus. |
| Typed reducer is perfect | Compute state in code, and let the LLM explain it. |
| Rereading is most accurate | Call tools again for live data each turn instead of trusting memory. |
| Guard withholds instead of guessing | When evidence is missing, say "not found". Never fall back to the model's own knowledge for account facts. |
