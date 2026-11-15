"""Which language a text is in, well enough to keep the checks honest.

Every check in finbot reads English: status words, retry advice, promises, escalation
rules. An answer in Spanish would pass them unread, and a complaint in German would not
be sent to a person. So a non-English message goes to a person with a reply in the user's
language, and a non-English answer is blocked.

No dependency and no model call: common words per language, plus a script test for
languages written outside the Latin alphabet. Short or mixed text counts as English.
"""
from __future__ import annotations

import re

_WORDS = {
    "en": "the is was why what how my payment card declined did does it this that for with and of to you can "
          "please not have has been were are transaction retry failed",
    "es": "el los las que por para con una pero porque cómo qué está fue mi pago tarjeta rechazado rechazada "
          "gracias hola responde español transacción reembolso quiero necesito",
    "fr": "le les des est pour avec une mais pourquoi comment été mon paiement carte refusé refusée merci "
          "bonjour je vous nous pas réponds français remboursement veux",
    "de": "der das und ist nicht warum wie wurde meine zahlung karte abgelehnt danke hallo ich sie mit für "
          "eine antworte deutsch rückerstattung bitte möchte",
    "pt": "os que por para com uma mas porquê porque como foi meu pagamento cartão recusado recusada obrigado "
          "olá não responda português transação reembolso quero preciso",
}
WORDS = {lang: frozenset(w.split()) for lang, w in _WORDS.items()}
_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


def detect(text: str, min_hits: int = 2) -> str:
    """"en", "es", "fr", "de", "pt", or "other" (a non-Latin script)."""
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) >= 4 and sum(ord(ch) > 0x24F for ch in letters) / len(letters) > 0.3:
        return "other"
    tokens = [t.lower() for t in _TOKEN.findall(text)]
    hits = {lang: sum(t in words for t in tokens) for lang, words in WORDS.items()}
    best = max((lang for lang in hits if lang != "en"), key=lambda lang: hits[lang])
    return best if hits[best] >= min_hits and hits[best] > hits["en"] else "en"
