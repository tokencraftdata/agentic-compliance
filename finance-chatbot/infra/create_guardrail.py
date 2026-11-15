"""Create the Bedrock guardrail used by finbot (run once per environment).

  python infra/create_guardrail.py --region us-east-1

Prints the guardrail id and version; export them as FINBOT_GUARDRAIL_ID /
FINBOT_GUARDRAIL_VERSION. Tune topics and thresholds per business line.
"""
import argparse

import boto3

TOPICS = [
    {"name": "InvestmentAdvice", "type": "DENY",
     "definition": "Recommendations about buying, selling or holding securities, crypto or other investments.",
     "examples": ["Should I buy Tesla stock?", "Which crypto will go up?"]},
    {"name": "LegalTaxAdvice", "type": "DENY",
     "definition": "Personalised legal or tax advice.",
     "examples": ["How do I avoid paying tax on this?", "Can I sue my customer?"]},
    {"name": "OffTopic", "type": "DENY",
     "definition": "Requests unrelated to payments, transactions, accounts or the company's financial products.",
     "examples": ["Write me a poem", "Who will win the election?"]},
]

PII = [
    {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
    {"type": "CREDIT_DEBIT_CARD_CVV", "action": "BLOCK"},
    {"type": "CREDIT_DEBIT_CARD_EXPIRY", "action": "ANONYMIZE"},
    {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "BLOCK"},
    {"type": "US_BANK_ACCOUNT_NUMBER", "action": "ANONYMIZE"},
    {"type": "PIN", "action": "BLOCK"},
    {"type": "PASSWORD", "action": "BLOCK"},
    {"type": "AWS_ACCESS_KEY", "action": "BLOCK"},
    {"type": "AWS_SECRET_KEY", "action": "BLOCK"},
    {"type": "EMAIL", "action": "ANONYMIZE"},
    {"type": "PHONE", "action": "ANONYMIZE"},
]

# Patterns Bedrock's built-in PII types do not cover. finbot also masks these locally
# (guardrails.mask_pii) before any text reaches Bedrock, the model, storage or logs.
REGEXES = [
    {"name": "CardTrackData", "description": "Magnetic-stripe track 1 or 2 data",
     "pattern": r"%?B\d{12,19}\^[^^]{0,26}\^\d{4}|;?\d{12,19}=\d{4}", "action": "BLOCK"},
    {"name": "CardSecurityCode", "description": "CVV/CVC phrasings the PII type can miss",
     "pattern": r"(?i)\b(cvv2?|cvc2?|security code)\b\W{0,3}(is\W{1,3})?\d{3,4}\b", "action": "BLOCK"},
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--name", default="finbot-guardrail")
    a = p.parse_args()
    client = boto3.client("bedrock", region_name=a.region)
    g = client.create_guardrail(
        name=a.name,
        description="Finance support chatbot: topic, PII, prompt-attack and grounding policy",
        topicPolicyConfig={"topicsConfig": TOPICS},
        contentPolicyConfig={"filtersConfig": [
            {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
            {"type": "MISCONDUCT", "inputStrength": "MEDIUM", "outputStrength": "MEDIUM"},
            {"type": "HATE", "inputStrength": "MEDIUM", "outputStrength": "MEDIUM"},
            {"type": "INSULTS", "inputStrength": "MEDIUM", "outputStrength": "MEDIUM"},
        ]},
        sensitiveInformationPolicyConfig={"piiEntitiesConfig": PII, "regexesConfig": REGEXES},
        contextualGroundingPolicyConfig={"filtersConfig": [
            {"type": "GROUNDING", "threshold": 0.75},
            {"type": "RELEVANCE", "threshold": 0.5},
        ]},
        blockedInputMessaging=("I can help with payments, transactions, and account questions. "
                               "I can't help with that request."),
        blockedOutputsMessaging="I couldn't verify an answer from our records, so I won't guess.",
    )
    v = client.create_guardrail_version(guardrailIdentifier=g["guardrailId"], description="initial")
    print(f"FINBOT_GUARDRAIL_ID={g['guardrailId']}\nFINBOT_GUARDRAIL_VERSION={v['version']}")


if __name__ == "__main__":
    main()
