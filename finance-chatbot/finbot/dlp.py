"""Data-loss prevention at the service boundary: every request in, every response out.

finbot already masks card data on each internal path (finbot/pii.py). This is the last
check at the edge, so a path that was missed still cannot leak:

    request   scanned and masked before anything stores or forwards it
    response  every string in the response scanned and masked before it leaves.
              Card data found in a response means an internal path failed to mask it:
              it is masked, counted and audited as `egress_leak` (page someone).

Categories and what happens to them:

    pci   card number, card-like number, track data, CVV, expiry, PIN    mask (pii.py)
    pii   SSN, IBAN (mod-97 checked), US bank routing (ABA checksum) + account number
                                                                         mask
    pii   email, phone                                                   detect only: answers legitimately
                                                                         contain the support contact
    secret  cloud access keys, private keys, bearer tokens               mask

Reports hold categories and counts, never the values.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .pii import mask_pii

IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?)\b")
ROUTING_ACCOUNT = re.compile(r"\b(?:routing|aba)(?: number| no\.?)?\W{0,3}(\d{9})\b"
                             r"(?:[^\d\n]{0,40}\b(?:account|acct)(?: number| no\.?)?\W{0,3}(\d{4,17})\b)?", re.I)
ACCOUNT = re.compile(r"\b(?:bank )?(?:account|acct)(?: number| no\.?| #)\W{0,3}(\d{6,17})\b", re.I)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# A period may end the sentence after the number; only ".<digit>" (a longer number) rules it out.
PHONE = re.compile(r"(?<![\w.])(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\w|\.\d)")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")
BEARER = re.compile(r"\b(?:Bearer\s+)?eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
REDACTED = "[REDACTED]"


def _iban_ok(raw: str) -> bool:
    s = raw.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    n = "".join(str(int(c, 36)) for c in s[4:] + s[:4])
    return int(n) % 97 == 1


def _aba_ok(d: str) -> bool:
    w = (3, 7, 1) * 3
    return sum(int(c) * k for c, k in zip(d, w, strict=True)) % 10 == 0


@dataclass
class DlpReport:
    found: Counter = field(default_factory=Counter)

    def add(self, category: str, n: int = 1) -> None:
        if n:
            self.found[category] += n

    @property
    def pci(self) -> bool:
        return any(k.startswith("pci:") for k in self.found)

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.found.items()))


def scan_text(text: str, report: DlpReport) -> str:
    """Masked text; what was found is added to `report`."""
    text, reasons = mask_pii(text)
    for r in reasons:   # pii.py reasons: pii:card, pii:cvv, pii:ssn, secret:aws_key, ...
        report.add(r if r.startswith("secret:") or r == "pii:ssn" else "pci:" + r.split(":", 1)[1])

    def sub(rx: re.Pattern, category: str, ok=lambda m: True) -> None:
        nonlocal text

        def repl(m: re.Match) -> str:
            if not ok(m):
                return m.group(0)
            report.add(category)
            return REDACTED
        text = rx.sub(repl, text)

    sub(PRIVATE_KEY, "secret:private_key")
    sub(BEARER, "secret:bearer_token")
    sub(IBAN, "pii:iban", lambda m: _iban_ok(m.group(1)))
    sub(ROUTING_ACCOUNT, "pii:bank_account", lambda m: _aba_ok(m.group(1)))
    sub(ACCOUNT, "pii:bank_account")
    report.add("pii:email", len(EMAIL.findall(text)))     # detect only
    report.add("pii:phone", len(PHONE.findall(text)))     # detect only
    return text


def scan(value: Any, report: DlpReport | None = None) -> tuple[Any, DlpReport]:
    """Every string inside `value` (dicts, lists, nested) scanned and masked."""
    report = report if report is not None else DlpReport()
    if isinstance(value, str):
        return scan_text(value, report), report
    if isinstance(value, dict):
        return {k: scan(v, report)[0] for k, v in value.items()}, report
    if isinstance(value, (list, tuple)):
        return [scan(v, report)[0] for v in value], report
    return value, report
