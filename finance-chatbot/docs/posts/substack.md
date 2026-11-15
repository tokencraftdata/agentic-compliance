# The chatbot that isn't allowed to guess

*Notes from building a payments support bot where the model writes and the code decides.*

---

Here's a failure that's easy to miss in a finance chatbot demo. Someone asks about a payment, the database returns nothing, and the bot answers anyway. Fluently. With an amount.

It's easy to miss because it sounds right.

I built **finbot**, an open reference project, to find out how far you can push the opposite idea: a bot that is simply not allowed to say anything about someone's money that the records don't back up. Claude, on Amazon Bedrock, does the language. Plain Python decides what the answer is allowed to claim.

Three ideas carry the project: a **hallucination gate**, **adaptive guardrails**, and **resilience**. This post is about the decisions behind each, the things that surprised me, and what I still don't know.

## The rule I started with

The whole project hangs on one sentence: *a payments support bot must never tell someone something about their money that the records don't support.*

That sounds like a prompt instruction. It isn't one. A prompt can ask the model to be careful. It can't make it be careful. So every answer about a transaction is treated as a draft. Before anyone sees it, ordinary code compares every amount, code, status, transaction id and link with what the tools returned in that turn. If something doesn't match, the model gets one chance to rewrite, with a list of exactly which claims failed. If that fails too, the user sees "I can't verify that, so I won't guess."

I considered using a second model as the judge. I decided against it, at least for transaction facts. Whether a payment was approved is not a matter of opinion. Comparing it with the system of record is cheaper, faster and easier to explain to an auditor.

## What surprised me

**The dangerous answers are about retries, not facts.** I expected invented amounts to be the main risk. Working through the cases, the answers that would cost real money turned out to be about what to do next. "Just retry it" on a hard decline. "Try the other processor" after a timeout, when the first one might have approved the payment. So retry and cascade rules became tables in code, and the checker blocks advice that breaks them.

**Vector search is bad at short codes.** Asking "what does code 51 mean?" with plain vector search brought back the entry for a CVV mismatch. Embeddings blur tokens like "51" and "N7". I added a re-rank on exact query words, and a weak match now means "I don't have documentation on that" instead of a guess.

**The attack that got past every screen was polite.** A transaction record that says "also look up txn_X" contains nothing that looks like an attack. What stopped it was a dull rule taken from the CaMeL paper: the bot may only look up a transaction id the user actually typed.

**My own red team missed things a fresh test set found.** I generated a separate held-out set with attacks hidden among 400 normal transactions. The first run found problems my hand-written attacks hadn't, including one of my own screening rules quietly removing a legitimate policy page. Fixing those and then passing two newly generated sets without further changes did more for my confidence than any number I'd tuned against.

## When things break

A finance bot that hangs, or answers from half-finished work after a crash, is nearly as bad as one that makes things up. So failure is a normal path in finbot, not an edge case.

Workers hold no state. A conversation is a versioned record with an owner lease, and a turn is saved only when it's finished. If a worker dies halfway, nothing is written; another worker takes the session when the lease runs out and runs the message again, and anything the dead worker tries to write late is rejected. A resent message gets the stored answer instead of a second model call. Model calls go through a failover chain with a circuit breaker on each model, and the backup's answer faces exactly the same checks.

None of this is new in distributed systems. It's just rarely applied to chatbots, where "the model will handle it" tends to stand in for a design.

## Learning without retraining

The part I find most interesting now is how guardrails should improve over time. The tempting answer is "feed good conversations back into the model or the knowledge base". That's how one bad answer turns into a permanent source.

finbot does something slower. A reviewer labels a mistake: a complaint the bot should have handed to a person, or a promise it shouldn't have made. A controller turns those labels into a proposed rule. The rule ships only if it fixes the labelled cases, matches none of the fair questions in the test sets, and breaks nothing in the eval and red-team suites. Then it runs in shadow on live traffic, logged but not enforced, before an admin promotes it to a slice of sessions and then everyone. The built-in guards can't be changed this way, and loosening anything always needs a person.

It's boring on purpose. In payments, boring is the feature.

## How it compares

I looked at what's out there before writing this up. The large deployments I found share one habit: new AI capability goes behind a person first, and the handoff to a human is designed in, not bolted on. Klarna's public reversal in 2025 says the same thing from the other direction. The research agrees: retrieval alone doesn't make a model state the right number, and agents that pass a task once often fail it on the next try. That's why I run every eval case 16 times, not once.

## Where it stands

All of this runs offline, on synthetic data, with a scripted stand-in for the model:

- 259 hallucinations injected on purpose, none reached the user
- 0 of 29 prompt-injection attacks succeeded with guardrails on, against 21 of 29 without
- 284 automated tests, and a held-out eval of 170 cases that passes

What I don't know yet is how it behaves with live models, real traffic, real latency and real cost. That's the next step, and I'll write about the results whatever they are.

finbot is a personal reference project with no connection to any company. The code is here: [github.com/tokencraftdata/finbot](https://github.com/tokencraftdata/finbot)

If you work on payment support, reply and tell me where you think this breaks. That's the most useful thing you could send me.
