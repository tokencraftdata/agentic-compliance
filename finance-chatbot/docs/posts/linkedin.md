A payments chatbot has one job that matters more than sounding helpful: never tell someone something about their money that the records don't back up.

Most finance bots fail that in the same few ways. A lookup comes back empty and the model fills the gap. It says "just retry it" on a decline that will never succeed. It suggests sending a timed-out payment to another processor, and the customer gets charged twice.

So I built finbot, an open reference project in Python on Amazon Bedrock. Claude writes the answer. Code decides what it's allowed to claim.

→ Every amount, code, status and link in an answer is checked against the tool results before anyone sees it
→ Retry and processor-cascade rules live in code, not in the prompt
→ Complaints, unauthorised payments and hardship go to a person
→ Promises the bot can't keep ("you'll get a refund") are blocked
→ Sessions survive a worker crash without answering twice

Offline results on synthetic data: 238 hallucinations injected on purpose, none reached the user. 0 of 29 prompt-injection attacks succeeded with guardrails on, against 21 of 29 without.

The numbers come from a scripted model, not live traffic. A live evaluation is next.

Write-up and code: [link]

If you work on payment support, I'd like to hear where this falls short.

#Fintech #Payments #GenerativeAI #AmazonBedrock #AIEngineering
