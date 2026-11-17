# Not Allowed to Guess: Record-Grounded Verification and Value Provenance for Fintech Support Chatbots

**Sandip Nandi** · Independent · November 2026
Code and data: https://github.com/tokencraftdata/finbot

## Abstract

A support chatbot for a financial product has one requirement that matters more than fluency:
it must not tell a customer something about their money that the records do not support.
Retrieval-augmented generation reduces but does not remove this risk, because the model still
writes the final sentence. We describe **finbot**, an open reference implementation in Python on
Amazon Bedrock, built around the opposite division of labour: the model drafts, and
deterministic code decides what may be said. Three mechanisms carry the design. A
**hallucination gate** checks every amount, currency, date, status, cause, reason code, record id,
link and contact detail in a draft against this turn's tool results; on failure it allows one
guided repair, then an answer built by code from the record, and only then a refusal.
**Domain plug-ins** let one core serve twelve fintech areas (accounts, transfers, KYC, lending,
card issuing, onboarding, disputes, mortgages, buy-now-pay-later, insurance claims,
subscriptions and card payments); each declares a field allow-list, a status vocabulary and a
policy table, and inherits every check. **Provenance** follows the data-flow ideas of CaMeL:
a tool may only be called with an identifier the user typed, and, new in this work, a value in
the answer may only rest on a field the operator's own systems set, never on text a merchant
or processor wrote. On synthetic data with a scripted model, 0 of 2,259 deliberately injected
hallucinations reached the user (0 of 3,774 on two data sets never used to tune the checks),
0 of 2,876 correct answers were blocked, and 0 of 34 prompt-injection attacks succeeded with
defences on, against 25 of 34 without. Value provenance closes a gap the other defences missed:
five answers repeating a planted, instruction-free refund amount, date, phone number or email
were all allowed before it and are all blocked after it. We have not yet evaluated against a
live model, and we state the limits of pattern-based checking plainly.

## 1. Introduction

Public failures of customer-facing chatbots share a shape. An airline's bot described a refund
policy that did not exist, and a tribunal held the airline to it (*Moffatt v. Air Canada*, 2024).
A software company's support bot invented a one-device policy (2025). A city's business
assistant told owners they could take workers' tips (2024). In each case the model produced a
fluent, confident statement with nothing behind it. In consumer finance the stakes are
regulatory as well as reputational: the US Consumer Financial Protection Bureau has said that
consumer-finance law applies to what a chatbot tells people, and that inaccurate answers can be
a violation (CFPB, 2023).

Support questions in finance have a property that makes a stronger guarantee possible: most of
them are about a **record**. "Why did my transfer come back?" has an answer in the transfer's
status and return code. "Is my claim approved?" has an answer in the claim's status. Where a
system of record exists, the answer can be checked against it before the customer sees it.

This paper makes four contributions:

1. **A verify-before-speak pipeline** in which every factual element of a draft answer is
   compared with this turn's tool results, with a bounded repair loop and a record-built
   fallback that keeps strictness from turning into refusals (Section 3).
2. **Domain plug-ins**: a declaration format that lets the same checks cover twelve fintech
   areas with no per-domain verification code (Section 4).
3. **Value provenance**: a separation of tool-result fields into operator-set and third-party
   text, so that text an attacker can write never counts as evidence for a stated fact
   (Section 5).
4. **An evaluation method** for this class of system that separates wrong answers from
   withholding, injects hallucinations deliberately, measures false blocks on every correct
   answer, uses a worst-case "gullible" model for prompt injection, and reports pass^k
   consistency (Section 6).

All data are synthetic. The policy tables are examples, not any institution's rules.

## 2. Problem and threat model

**Users.** Customers and support agents, authenticated by a verified token that fixes the tenant.
The bot is read-only: requests to change anything (cancel, refund, unblock, appeal) become
tickets that a person executes.

**Failure we must prevent.** A customer-visible statement about a record that the record does
not support: a wrong amount or date, a decision stated before it is made ("you're approved"
while underwriting is open), a guessed cause ("probably a sanctions check"), unsafe advice
("just retry" on a hard decline; "cancel it" on a settled real-time payment), a promise nobody
made, or a contact detail that sends the customer to a fraudster.

**Adversary.** Anyone who can write text the bot reads: the user, a merchant (name and
statement descriptor), a payment processor (log messages), or an author of a help page. The
adversary cannot forge the session token or alter the operator's own record fields.

**Model.** Untrusted for facts. We assume the model may produce any string, including one an
injection asked for; the evaluation in Section 6.3 uses a model that obeys every instruction it
sees.

## 3. Architecture

A turn passes through a fixed pipeline: risk lock, input guard, language check, learned input
policy, focus tracking, write-request detection, and only then the model. The model runs a
tool-use loop over read-only tools scoped to the session's tenant. Its final text goes to the
**hallucination gate**, a chain of verifiers:

| Verifier | What it enforces |
|---|---|
| Evidence | amounts, currencies, percentages, dates, times, card digits and brand, reason codes and record ids appear in this turn's tool data; a URL is a help page's own `source_url`; payment status polarity matches; no status claim without a lookup; retry and routing advice match the policy table |
| Reason | a cause stated in plain words ("insufficient funds") is the cause the record carries |
| Domain | for every plug-in record: the status claimed is the record's status (in any tense for a final decision, in the present tense for a transient state, and only when the sentence is about the record); the cause is one the record's reason keys carry; nested events (last payment, last authorization) are checked too |
| Policy | learned commitment rules: "you'll get a refund", "guaranteed" |
| Tickets | a ticket id or "I've raised a ticket" only when code opened one |
| Language | the answer is in English, the only language the checks can read |

A block from any verifier blocks the answer. The gate then asks the model **once** to rewrite
using only tool facts, and re-verifies against the repair's own tool results, so a stale earlier
result cannot vouch for a new claim. If the repair also fails, code builds an answer from the
record (status, amount, code and the policy's next step for the reader's role) and verifies
that too. The customer sees "I won't guess" only when code cannot build a verifiable answer from a
single record: a help-page question, a question about several records, or a record whose fields
fail validation. Two such answers in a row hand the conversation to a person.

The record-built fallback addresses the obvious cost of strictness. With a faulty scripted
model and repair switched off, it reduced refusals from 39.9% to 8.9% of turns.

Around the gate sit conventional defences: a field allow-list (hidden fields such as review
reasons, credit and fraud scores and adjuster notes never reach the model, so the model cannot
leak or hint at them), injection screening of user text and tool results, spotlighting with a
per-turn nonce (Hines et al., 2024), PII masking, a prompt-leak canary, and a session risk score
for attacks spread over several turns.

## 4. Domain plug-ins

The core does not know what a loan or a claim is. A domain is a frozen declaration:

```python
register(Domain(
    name="subscriptions", prefix="sub", tool="get_subscription", id_arg="subscription_id",
    fields=frozenset({...}),                       # allow-list
    statuses={"cancelled": "cancelled", ...},      # answer word -> record value
    policy={"INVOICE_CARD_DECLINED": Rule(meaning, customer_step, support_step, resend=True)},
    reason_key=..., summary=...,                   # record -> policy key; record -> one sentence
    causes={...}, cause_phrases={...},             # how a cause is said in words
    implied={...}, status_causes={...},            # statuses that imply others or carry a cause
))
```

From this the core derives the tool schema, tenant scoping, argument provenance, policy
guidance attached to the record, the status, cause, reason-key and detail checks, ticket
handling for write requests and the record-built answer. Each domain exists to stop one or two
costly answers: a respond-by date not on a dispute record; any mention of foreclosure on a
mortgage; "you're covered" on an insurance claim, which is a coverage opinion rather than a
record fact; "your subscription was cancelled" when it only ends at period end.

Adding eleven domains to an engine first built for card payments taught three lessons. Cause
phrases are global, so a loose phrase in one domain appears as a false block in another (a
dispute's "3-D Secure" evidence first read as a payment authentication cause). Some statuses
imply earlier ones (a paid claim was approved) and need an explicit `implied` entry. And some
statuses carry their own cause (a past-due loan is delinquent).

**Where the design does not fit.** It checks facts against a record. Open-ended advice, rules
interpreted from prose, discretionary decisions and diagnosis have no record to check against;
there finbot uses denied topics, citation requirements, tickets or handoff instead.

## 5. Provenance

### 5.1 Argument provenance

Following CaMeL (Debenedetti et al., 2025) and the design patterns of Beurer-Kellner et al.
(2025), untrusted data must not choose control flow. A record that says "also look up txn_X"
contains nothing a screen recognises as an attack. finbot refuses any record lookup whose
identifier the user did not type in this session. In our red team, two such pivot attacks pass
screening in every configuration; argument provenance alone stops both.

### 5.2 Value provenance

Argument provenance governs which tool runs. It says nothing about **which values may appear in
the answer**. Before this work, the detail checks searched every string in a tool result. That
included text the operator does not control: a merchant's name and statement descriptor, and a
processor's free-text log message.

A planted value need not look like an attack. Consider a descriptor:

> `NORTHWIND REFUND 480.00 USD BY 2026-12-01 HELP 1-888-555-0142 refunds@northwind-help.example`

It contains no instruction, so injection screening passes it. A model that trusts it answers
"A refund of $480.00 is on its way by 2026-12-01; call 1-888-555-0142." Every detail in that
answer appeared in the tool result, so every check passed. Phone numbers and emails were not
checked at all.

Value provenance labels each field by who sets it:

- **Operator-set:** status, amounts, dates, codes, balances, policy guidance computed by our
  own code, and help-page text with its source URL.
- **Third-party:** merchant names, descriptors, processor log messages, memos and similar
  free-text fields, at any depth (including nested events).

The detail and amount checks now read a *trusted view* of the tool data in which third-party
strings are replaced. Third-party text still reaches the model, which may say "the charge at
Northwind Grocers", but it cannot vouch for an amount, date, time, percentage, code, card digit
or currency. A phone number or email in an answer must appear in an operator-set field, such as
a help page; the block reason names only the kind ("phone"), so the audit log does not store the
lure.

This is a small change (one module and about five lines in the checks) because the gate already
existed: provenance only narrows what counts as evidence. It is a deliberately partial version
of CaMeL, which tracks capabilities through an interpreter. For a read-only bot whose only sink
is the answer text, labelling sources at the tool boundary and checking at the single sink
gives most of the benefit at little cost.

**Result.** Five answers built from the descriptor and log message above (refund amount, date,
a log-message credit amount and date, phone number, email) were all allowed by the previous
checks and are all blocked now. On both data sets, every failed payment and every domain record
was asked about by both roles (2,876 correct answers); none is newly blocked. The evaluation
sets, stress tests, consistency runs and red team are unchanged.

## 6. Evaluation

All experiments are offline, on synthetic data, with scripted models. We separate *wrong
answers* from *withholding* (blocked, abstained, unavailable), since a system that refuses
everything would otherwise look perfect.

### 6.1 Data

A generator produces transactions, processor logs, help pages and records for every domain,
with a development split and a held-out split from different seeds: 176 domain records per
split, 21 help pages, 41 development and 185 held-out evaluation cases. Two further data sets
(seeds 4242 and 9001) were never used to tune the checks; they are scored with no changes. Seeds that informed a fix are
reported as no longer clean.

### 6.2 Hallucination stress test

A faulty model corrupts answers in twelve ways: a flipped status, an invented amount or link, a
fact carried over from an earlier record, a status claimed without a lookup, unsafe retry advice,
an invented processor cascade or outage, bad cascade advice, a false promise, a fake ticket and a
guessed reason. We
count how many corrupted answers reach the user.

| Measure | Result |
|---|---|
| Evaluation cases | 41/41 development; 185/185 held-out; 192/192 and 184/184 on the two later data sets |
| Injected hallucinations reaching the user | 0 of 392 development; 0 of 1,867 held-out; 0 of 3,774 on the two later data sets |
| Correct answers wrongly blocked | 0 of 2,876 |
| Consistency (pass^k, Yao et al., 2024), half of all answers corrupted | pass^16 = 1.000 on the development set |

The stress test found real bugs during development, each fixed with a test: "Don't worry,
you're covered" read as negated because of "Don't"; a declined automatic payment restated as
approved slipped past because it was an event inside a record rather than the record's status;
a paid claim was flagged for being called approved.

### 6.3 Prompt injection

We use a **gullible model** that obeys any instruction it can see, so the score measures the
system, not the model's resistance. The set has 34 attacks drawn from published taxonomies
(direct, indirect through records and logs, knowledge-base poisoning, encoded and split
payloads, link exfiltration, pivots) and 7 benign look-alikes such as a merchant named
"System Repair Shop".

| Configuration | Attacks succeeded | Benign answered |
|---|---|---|
| All defences | 0/34 | 7/7 |
| No screening (gate and provenance only) | 0/34 | 7/7 |
| No defences | 25/34 | 7/7 |

Five multi-turn attacks in the style of Crescendo (Russinovich et al., 2025) all failed with
defences on, each stopped at the harmful turn; with no defences, four of five succeeded. A separate generated set hides 21 attacks among 400 normal
transactions with ten benign look-alikes; its first run found problems the hand-written set
had missed, including a screening rule that removed a legitimate help page.

The "no screening" row is the important one: the gate and provenance alone stop every attack,
because an injection that reaches the model still cannot make the bot state an unsupported
fact, fetch another record, cite a planted link or ask for a secret. The red team cannot measure
value provenance, since a gullible model follows instructions and a planted fact contains none;
Section 5.2's tests cover it.

## 7. Related work

**Grounding and verification.** Post-hoc revision against retrieved evidence (RARR, Gao et al.,
2023; CRITIC, Gou et al., 2024) and claim-level fact-checking (FActScore, Min et al., 2023;
MiniCheck, Tang et al., 2024; Claimify, Metropolitansky and Larson, 2025) check free text
against documents, usually with a model. finbot checks a narrower class of claims against
structured records with code, which trades coverage for determinism. Conformal factuality
(Mohri and Hashimoto, 2024) drops low-confidence claims with a statistical guarantee; dropping
only failing sentences is a natural next step for finbot's gate.

**Prompt-injection defence.** Indirect injection (Greshake et al., 2023), injection through tool
results (InjecAgent, Zhan et al., 2024) and knowledge-base poisoning (PoisonedRAG, Zou et al.,
2025) define the threat. Spotlighting (Hines et al., 2024) and the instruction hierarchy
(Wallace et al., 2024) reduce what reaches the model. CaMeL (Debenedetti et al., 2025) and the
design patterns of Beurer-Kellner et al. (2025) limit what an injection can do by construction;
finbot adopts both halves of CaMeL's idea at the scale a read-only bot needs. Concurrent work on
argument-level provenance for agent tool calls (arXiv:2605.11039, 2026) is closely related to
Section 5.1; we have not yet compared the two in detail.

**Runtime rules for agents.** AgentSpec (Wang et al., 2026) enforces declarative trigger-condition-
action rules at runtime and reports that model-drafted rules reach high precision but lower
recall. finbot's policy tables and write-requests-become-tickets rule are a domain-specific
instance; model-drafted tables with human review are how a deployment would populate them.

**Evaluation.** τ-bench (Yao et al., 2024) introduced pass^k and database-state scoring for tool
agents with policies; AgentDojo (Debenedetti et al., 2024) measures utility under attack. We
borrow pass^k and the utility-under-attack framing.

## 8. Limitations

- **No live model.** Every number comes from scripted models on synthetic data. A live
  evaluation on Bedrock is the next step, and real models will produce wordings the patterns
  have not seen.
- **Pattern-based checks.** Status and cause checks match phrases. An unfamiliar wording can pass
  (a miss) or a correct answer can be blocked (a false block). We measure false blocks on every
  correct answer we can generate, but our generator shares our assumptions.
- **Self-authored attacks.** The red team was written by the author, and no attacker adapted to
  the defences (Nasr et al., 2025, show adaptive attackers break most published defences).
- **Help-page answers.** Answers drawn from prose must cite a page, but the page's support for
  the claim is not checked. Claim-level checking against the cited page is future work.
- **Value provenance is partial.** It covers the answer, not tool arguments beyond record
  identifiers (the read-only knowledge search and processor-health lookups are unchecked), and
  it trusts help pages, so knowledge-base poisoning is handled by screening and audits, not by
  provenance.
- **Scope.** Read-only, English only; other languages go to a person.

## 9. Conclusion

When a system of record exists, a support chatbot does not have to be trusted to tell the
truth; its answers can be checked before anyone sees them. The ingredients are ordinary: a
field allow-list, a status vocabulary, a policy table, and code that compares the draft with the
record. Value provenance shows the same principle applied one level deeper: it matters not only
*whether* a value appears in the evidence, but *who wrote it*. The cost of strictness, refusals,
can be cut by letting code say what the record shows. What remains open is how these checks
hold up against a live model and an adaptive attacker.

## References

- Beurer-Kellner, L. et al. (2025). Design Patterns for Securing LLM Agents against Prompt Injections. arXiv:2506.08837.
- Consumer Financial Protection Bureau (2023). Chatbots in consumer finance. Issue spotlight, June 2023.
- *The Granularity Mismatch in Agent Security: Argument-Level Provenance Solves Enforcement and Isolates the LLM Reasoning Bottleneck* (2026). arXiv:2605.11039.
- Debenedetti, E. et al. (2024). AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents. NeurIPS Datasets and Benchmarks. arXiv:2406.13352.
- Debenedetti, E., Shumailov, I., Tramèr, F. et al. (2025). Defeating Prompt Injections by Design. arXiv:2503.18813.
- Gao, L. et al. (2023). RARR: Researching and Revising What Language Models Say, Using Language Models. ACL. arXiv:2210.08726.
- Gou, Z. et al. (2024). CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing. ICLR. arXiv:2305.11738.
- Greshake, K. et al. (2023). Not What You've Signed Up For: Compromising Real-World LLM-Integrated Applications with Indirect Prompt Injection. arXiv:2302.12173.
- Hines, K. et al. (2024). Defending Against Indirect Prompt Injection Attacks With Spotlighting. arXiv:2403.14720.
- Metropolitansky, D. and Larson, J. (2025). Towards Effective Extraction and Evaluation of Factual Claims. ACL. arXiv:2502.10855.
- Min, S. et al. (2023). FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation. EMNLP. arXiv:2305.14251.
- *Moffatt v. Air Canada*, 2024 BCCRT 149.
- Mohri, C. and Hashimoto, T. (2024). Language Models with Conformal Factuality Guarantees. ICML. arXiv:2402.10978.
- Nasr, M. et al. (2025). The Attacker Moves Second. arXiv:2510.09023.
- Russinovich, M., Salem, A. and Eldan, R. (2025). Great, Now Write an Article About That: The Crescendo Multi-Turn LLM Jailbreak Attack. USENIX Security. arXiv:2404.01833.
- Tang, L., Laban, P. and Durrett, G. (2024). MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents. EMNLP. arXiv:2404.10774.
- Wallace, E. et al. (2024). The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions. arXiv:2404.13208.
- Wang, H., Poskitt, C. M. and Sun, J. (2026). AgentSpec: Customizable Runtime Enforcement for Safe and Reliable LLM Agents. ICSE. arXiv:2503.18666.
- Yao, S., Shinn, N., Razavi, P. and Narasimhan, K. (2024). τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains. arXiv:2406.12045.
- Zhan, Q. et al. (2024). InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated LLM Agents. arXiv:2403.02691.
- Zou, W. et al. (2025). PoisonedRAG: Knowledge Corruption Attacks to Retrieval-Augmented Generation of Large Language Models. USENIX Security. arXiv:2402.07867.

*All references except RARR, CRITIC and FActScore were checked against their arXiv, publisher or
tribunal pages in November 2026; those three are cited from memory and should be checked before
submission.*
