"""Cardholder data and secrets: detection and masking, in one place.

The rule (PCI DSS): card data is masked BEFORE text is stored, logged, queued, shown to
a person or sent to a model, on every path, including turns that are blocked or fail.
Every module that stores or forwards text calls mask_pii / mask_value from here.

    PAN, Luhn-valid           -> [CARD ••••1234]    (last four only)
    card-shaped, fails Luhn   -> [NUMBER REDACTED]  (a mistyped card is still card data)
    track 1 / track 2         -> [TRACK DATA REDACTED]
    CVV, expiry, PIN, SSN, AWS key -> [REDACTED]
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Cardholder data. Masking runs on every path that stores or forwards text (user input,
# model output, tool results, ingestion, labels, journals, queues), not only on input.
ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")
# A run of digit groups that could be a PAN: one 13-19 digit group, or groups of 3-6 digits
# joined by up to two separators (space, tab, dot, dash, underscore, slash) or a line break.
# Groups of 1-2 digits (dates, amounts) never join a run.
PAN_RUN = re.compile(r"(?<!\d)\d{3,19}(?:(?:[ \t._/\-]{1,2}|\r?\n)\d{3,6})*(?!\d)")
TRACK = re.compile(r"%?B\d{12,19}\^[^^\n]{0,26}\^\d{4}[^?\s]*\??|;?\d{12,19}=\d{4}\d*\??")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
AWS_KEY = re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b")
_IS = r"\W{0,3}(?:is|was|=|:)?\W{0,3}"
CVV = re.compile(r"\b(?:cvv2?|cvc2?|cid|csc|card verification (?:value|code)|security code)\b" + _IS +
                 r"\d{3,4}\b|\bon the back\b[^.\n\d]{0,20}\b\d{3,4}\b", re.I)
EXPIRY = re.compile(r"\b(?:exp(?:iry|iration)?(?: date)?|valid (?:thru|through|until)|expires?|expiring)\b" +
                    _IS + r"(?:0?[1-9]|1[0-2])\s?[/\-]\s?(?:\d{4}|\d{2})\b", re.I)
PIN = re.compile(r"\bpin(?: code| number)?\b" + _IS + r"\d{4,6}\b", re.I)

# Reasons that mean the user typed card data (the reply then tells them not to).
CARD_REASONS = frozenset({"pii:card", "pii:card_like", "pii:track_data", "pii:cvv", "pii:expiry", "pii:pin"})


def _luhn(digits: str) -> bool:
    d = [int(c) for c in digits][::-1]
    total = sum(d[0::2]) + sum(sum(divmod(2 * x, 10)) for x in d[1::2])
    return total % 10 == 0


def _ascii_digits(text: str) -> str:
    """Full-width and other Unicode decimal digits -> ASCII, so "４１１１" can't slip past."""
    return "".join(str(unicodedata.decimal(c)) if not c.isascii() and c.isdecimal() else c for c in text)


def _pan_windows(groups: list[str]) -> list[tuple[int, int]]:
    """Consecutive group ranges [i, j) that form a Luhn-valid 13-19 digit number, longest
    first, without overlap. "order 1234 4111 1111 1111 1111" masks only the card."""
    found: list[tuple[int, int]] = []
    i = 0
    while i < len(groups):
        best = None
        for j in range(len(groups), i, -1):
            digits = "".join(groups[i:j])
            if 13 <= len(digits) <= 19 and _luhn(digits):
                best = (i, j)
                break
        if best:
            found.append(best)
            i = best[1]
        else:
            i += 1
    return found


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Mask cardholder data and secrets. Idempotent: masked output passes through unchanged.

    PAN -> [CARD ••••1234] (Luhn-valid) or [NUMBER REDACTED] (15-19 digits in card-like
    groups that fail Luhn: a mistyped card is still card data). Track data, CVV, expiry and
    PIN are removed outright: sensitive authentication data must never be stored.
    """
    reasons: list[str] = []
    text = _ascii_digits(ZERO_WIDTH.sub("", text))

    def track(m: re.Match) -> str:
        reasons.append("pii:track_data")
        return "[TRACK DATA REDACTED]"

    def pan(m: re.Match) -> str:
        run = m.group(0)
        groups = re.findall(r"\d+", run)
        seps = re.split(r"\d+", run)[1:-1]
        wins = _pan_windows(groups)
        if not wins:
            digits = "".join(groups)
            if 15 <= len(digits) <= 19 and (len(groups) == 1 or all(3 <= len(g) <= 6 for g in groups)):
                reasons.append("pii:card_like")
                return "[NUMBER REDACTED]"
            return run
        out, k = [], 0
        for i, j in wins:
            out.append("".join(g + seps[n] for n, g in enumerate(groups[k:i], start=k)))
            reasons.append("pii:card")
            out.append(f"[CARD ••••{''.join(groups[i:j])[-4:]}]")
            if j < len(groups):
                out.append(seps[j - 1])
            k = j
        out.append("".join(groups[n] + (seps[n] if n < len(seps) else "") for n in range(k, len(groups))))
        return "".join(out)

    text = TRACK.sub(track, text)
    text = PAN_RUN.sub(pan, text)
    for pat, tag in ((SSN, "pii:ssn"), (AWS_KEY, "secret:aws_key"), (CVV, "pii:cvv"),
                     (EXPIRY, "pii:expiry"), (PIN, "pii:pin")):
        if pat.search(text):
            reasons.append(tag)
            text = pat.sub("[REDACTED]", text)
    return text, list(dict.fromkeys(reasons))


def mask_value(value: Any) -> Any:
    """mask_pii over every string inside a JSON-like value (dicts, lists, scalars)."""
    if isinstance(value, str):
        return mask_pii(value)[0]
    if isinstance(value, dict):
        return {k: mask_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v) for v in value]
    return value
