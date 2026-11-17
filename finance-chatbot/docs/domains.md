# Domains: what finbot covers, how to add one, and where the design does not fit

finbot's core does not know what a bank account or an insurance claim is. Each subject area is a
**domain plug-in** in `finbot/domains/`, and the shared machinery (turn pipeline, hallucination
gate, session memory, argument provenance, tickets, record-built answers, crash recovery) treats
every domain the same way. A deployment chooses its domains with one setting:

```bash
FINBOT_DOMAINS=accounts,transfers,kyc,lending,issuing,onboarding,disputes,mortgages,bnpl,insurance,subscriptions,payments
```

Leave a name out to switch that area off. `payments` (card acceptance: declines, retries,
processor routing) is one domain among the others.

## 1. The domains that ship

| Domain | Id | Tool | The costly wrong answer it is built to stop |
|---|---|---|---|
| accounts | `acct_` | `get_account` | a guessed reason for a review ("probably a sanctions check"); a balance or hold date that is not on the record |
| transfers | `xfer_` | `get_transfer` | "resend it" on a hard return; "cancel it" on a settled real-time payment; a guessed compliance-hold reason |
| kyc | `kyc_` | `get_verification` | "you're verified" while the case is with a reviewer; a guessed screening hit |
| lending | `loan_` | `get_loan` | a predicted decision; a reason that is not on the record (the credit score is never shown to the model) |
| issuing | `card_` | `get_card` | a lost or stolen card called active; "just raise the limit" |
| onboarding | `app_` | `get_application` | a promised decision date; a guessed review reason |
| disputes | `dsp_` | `get_dispute` | a wrong respond-by date (late responses lose automatically); "you've won" before the network decides |
| mortgages | `mtg_` | `get_mortgage` | a payoff amount without its good-through date; any talk of foreclosure; a predicted loss-mitigation outcome |
| bnpl | `bnpl_` | `get_bnpl_plan` | a wrong due date or late fee; a waived fee |
| insurance | `clm_` | `get_claim` | "you're covered" (a coverage opinion, not a record fact); hints from adjuster notes or a fraud score the model never sees |
| subscriptions | `sub_` | `get_subscription` | "your subscription was cancelled" when it cancels at period end; an invented billing policy |
| payments | `txn_` | `get_transaction` + logs, routing | "retry now" on a hard decline; "send it to the other processor" after a timeout |

Every domain is synthetic: invented records, invented reason codes in a familiar style, and an
**example** policy table. Replace the tables with your own rules before using any of this.

## 2. What a domain declares

```python
register(Domain(
    name="subscriptions", noun="subscription", prefix="sub",
    tool="get_subscription", id_arg="subscription_id", source="billing_db",
    description="Fetch the authoritative billing record for one subscription ...",
    fields=frozenset({"subscription_id", "plan", "status", "price", ...}),   # allow-list
    statuses={"active": "active", "cancelled": "cancelled", ...},           # answer word -> record value
    open_statuses=frozenset(),                                               # undecided states
    policy={"INVOICE_CARD_DECLINED": Rule(meaning, customer_step, support_step,
                                          resend=True, resend_rule="...")},
    reason_key=_reason,       # record -> the policy key that applies now
    summary=_summary,         # record -> one validated sentence, or None
    causes={"INVOICE_CARD_DECLINED": "invoice_declined"},
    cause_phrases={"invoice_declined": r"\b(renewal|invoice) (payment )?(was )?declined\b"},
    implied={"paid": frozenset({"approved"})},          # optional: earlier states a status implies
    status_causes={"past_due": "delinquency"},          # optional: a cause the status itself carries
))
```

What the core then does for every domain, with no extra code:

| Mechanism | What it enforces |
|---|---|
| Field allow-list | a field not listed never leaves the tool: review reasons, credit and fraud scores, adjuster notes |
| Tenant scoping | the record must belong to the logged-in customer, or it does not exist |
| Argument provenance | the tool runs only with an id the user typed, never one read from a record |
| Policy guidance | the matching `Rule`, worded for the reader's role, is attached to the record |
| Status check | a status the record does not have is blocked: in any tense for a decision, in the present tense for a transient state, and only when the sentence is about the record |
| Cause check | a cause given in words must be one the record's reason keys (or status) carry; a cause no record can carry is always a guess |
| Reason-key check | a reason key in the answer must be in this turn's records |
| Detail checks | amounts, currencies, dates, times, percentages, card digits, links, phone numbers and emails must come from the records' own fields, not from third-party text such as a merchant's descriptor |
| Resend check | when `resend` is False, advice to send the same request again is blocked unless it carries an allowed qualifier |
| Write requests | "cancel / unblock / pause / appeal / extend" on any record becomes a ticket for a person |
| Record-built answer | when the model's answer can't be verified, `summary` plus the policy guidance is said instead of a refusal |

## 3. Adding a domain

1. Write `finbot/domains/<name>.py`: the `Rule` table, `_reason`, `_summary` and the `register(...)` call.
   Validate every field `_summary` repeats (`money`, `ok(DATE, ...)`, `ok(KEY, ...)`).
2. Import it in `finbot/domains/__init__.py` and add its name to `DEFAULT` and to `FINBOT_DOMAINS`.
3. Add its id prefix and tool to the routing line of the system prompt (`finbot/orchestrator.py`).
4. Generate records, a help page and eval cases in `scripts/generate_synthetic.py`.
5. Add tests for the one or two wrong answers the domain exists to stop.
6. Run the false-block probe: every record, asked as both roles, must pass. New cause phrases are
   global, so a phrase that is too loose shows up as a false block in *another* domain.

Lessons from adding eleven of these: keep cause phrases specific ("renewal payment declined", not
"declined"); give statuses that imply earlier ones an `implied` entry; let a status carry its own
cause with `status_causes`; and keep wording in policy text from tripping another domain's phrases
(a dispute's "3-D Secure" evidence first read as a payment's 3DS cause).

## 4. Beyond fintech: where the same design fits

The design needs three things: a **system of record** the bot can read, **reason codes** that
say why a record is in its state, and a **policy table** that says what may be said and done
about each code. Where those exist, the domain plug-in pattern carries over unchanged.

| Area | System of record | Reason codes and policy table | The costly wrong answer | Public evidence the risk is real |
|---|---|---|---|---|
| Airline rebooking, refunds, baggage | booking, ticket, bag tag | delay and cancellation codes; fare rules and compensation table | an invented refund or fare policy | *Moffatt v. Air Canada*, BC Civil Resolution Tribunal, 14 Feb 2024: the airline was held liable for a refund policy its chatbot invented |
| Healthcare claims, eligibility, prior authorization | claims and eligibility systems | public claim adjustment and remark codes; appeal-rights table | "you're covered" or "authorization approved" when it is not | CMS guidance to Medicare Advantage plans, Feb 2024: algorithms may assist but cannot alone decide coverage |
| Insurance claims | claim and policy administration | denial and pend codes; appeal table | invented coverage, a wrong payout | none found for a chatbot; shipped here as the `insurance` domain |
| E-commerce orders and returns | order, shipment and return systems | return reasons; returns-window table | an invented refund, price or promise | Chevrolet dealer chatbot "agreed" to sell a car for $1 after prompt injection, Dec 2023 |
| SaaS billing | subscriptions and invoices | dunning and decline codes; plan entitlements | an invented policy | Cursor's support bot invented a one-device policy, Apr 2025; shipped here as `subscriptions` |
| Logistics and customs | tracking events | exception and customs-hold codes | a wrong delivery date or duty | none found for wrong facts |
| Telecom and utilities billing | billing and outage systems | disconnect and outage codes | a wrong restore time or disconnection date | none found |

For consumer finance specifically, the US Consumer Financial Protection Bureau's June 2023
issue spotlight on chatbots says consumer-finance law applies to what a chatbot tells people,
and that inaccurate answers can be a violation.

## 5. Where the design does not fit

The design checks *facts* against a *record*. Where the answer is a judgement, there is nothing
to check against, and adding a policy table only blocks most output.

| Kind of question | Why it does not fit | What finbot does instead |
|---|---|---|
| Open-ended advice (investment, tax planning, legal strategy, clinical) | no record to compare with; the answer *is* the judgement | denied topics, before and after the model |
| Rules interpretation over prose ("can I take my workers' tips?") | free-text retrieval is not a record; the design helps only after the rules are reduced to a coded table | knowledge answers must cite a help page, or say there is no documentation |
| Discretionary decisions (claim adjudication, hardship approval, underwriting) | a person decides; a bot that decides is a different, regulated system | the bot reports the decision and its code; requests become tickets |
| Diagnosis and troubleshooting | hypotheses are not checkable facts | out of scope |
| Negotiation, distress, complaints | a correct fact is not the hard part | escalation rules hand these to a person |

The cautionary case for the second row is New York City's MyCity business chatbot, which The
Markup reported in March 2024 told businesses they could take workers' tips and refuse housing
vouchers. Retrieval over statutes and guidance did not stop it, because there was no record that
could say the answer was wrong.

## Sources

- *Moffatt v. Air Canada*, 2024 BCCRT 149: https://decisions.civilresolutionbc.ca/crt/crtd/en/525448/1/document.do
- The Markup on NYC MyCity, 29 Mar 2024: https://themarkup.org/news/2024/03/29/nycs-ai-chatbot-tells-businesses-to-break-the-law
- Cursor support bot, Apr 2025: https://www.eweek.com/news/cursor-ai-chatbot-hallucination-fake-policy
- CFPB, *Chatbots in consumer finance*, Jun 2023: https://files.consumerfinance.gov/f/documents/cfpb_chatbot-issue-spotlight_2023-06.pdf
- CMS FAQ to Medicare Advantage organizations, Feb 2024 (summary): https://www.dlapiper.com/insights/publications/2024/02/cms-issues-faq-to-medicare-advantage-organizations
- Chevrolet dealer chatbot, Dec 2023: https://incidentdatabase.ai/reports/3535

These were found through web search and secondary reporting; check the primary document before
citing a figure.
