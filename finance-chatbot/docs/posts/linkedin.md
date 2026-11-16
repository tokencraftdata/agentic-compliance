A fintech support chatbot has one job that matters more than sounding helpful: never tell someone something about their money that the records don't back up.

Finance bots tend to fail that in the same few ways. A lookup comes back empty and the model fills the gap. It says "just retry it" on a decline that will never succeed. It promises a refund nobody approved. It calls an application approved while underwriting is still open, or guesses why an account is under review. And when a model endpoint or a worker goes down mid-conversation, it hangs or answers twice.

I designed and built finbot, an open-source reference implementation in Python on Amazon Bedrock, around three ideas. It covers a fintech's support desk as plug-ins — bank accounts, transfers (batch, real-time, wire), issued cards, card payments, KYC, lending, onboarding — each one module with the record's fields, statuses, reason codes and a policy table:

1. A hallucination gate. Claude writes the answer, but code checks every amount, code, status and link against the records before anyone sees it. One guided repair, then an honest "I won't guess."

2. Adaptive guardrails. Rules improve from reviewer-labelled mistakes, not from raw chat and not by retraining. A new rule must pass the eval and red-team suites, run in shadow, then go out through a canary with an admin's approval.

3. Resilience. Stateless workers, sessions that survive a crash mid-turn, de-duplicated messages, and model failover with a circuit breaker per model.

Offline, on synthetic data: 2,259 hallucinations injected on purpose — wrong amounts, flipped decisions, invented outages, guessed review reasons — and none reached the user. 0 of 34 prompt-injection attacks succeeded with guardrails on, against 25 of 34 without.

The drawbacks, stated plainly: the checks are pattern-based, so an unfamiliar wording can get through. Strict means some chats still end in "I won't guess" or a handoff, though the bot now answers from the record itself before it refuses. It is read-only and English only: other languages go to a person. And these numbers come from a scripted model, not live traffic. A live evaluation is next.

Code and write-up: https://github.com/tokencraftdata/finbot

If you work on payment support or AI reliability, I'd like to hear where this falls short.

#Fintech #Payments #GenerativeAI #AmazonBedrock #AIReliability #Guardrails
