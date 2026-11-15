"""Keeping the sources honest: stale help pages and help pages that disagree with the code.

finbot repeats what its records and help pages say, so a wrong page becomes a wrong answer
with a citation. Two checks narrow that:

  stale_dates   at answer time: the "last updated" dates of cited pages older than the
                limit, so code can add a "may be out of date" note to the answer
  audit_pages   offline (scripts/kb_audit.py): pages past the limit, and decline-code
                sections whose "Retryable" line or meaning disagrees with the retry policy
                and code tables the checker enforces
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from .policy import POLICY
from .processors.codes import CANONICAL

_URL = re.compile(r"https?://[^\s)\]>\"']+")
_TAG = re.compile(r"<[^>]+>")
_SECTION = re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>(.*?)(?=<h[1-4]|$)", re.I | re.S)
_CODE_HEADING = re.compile(r"^\s*(?:code\s+)?([A-Z0-9_]{2,24})\b", re.I)
_RETRYABLE = re.compile(r"retryable:\s*(yes|no)\b", re.I)
_MEANING = re.compile(r"meaning:\s*([^.]+)\.", re.I)


def age_days(updated: str, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(updated)[:10])).days
    except ValueError:
        return None


def stale_dates(answer: str, tool_log: list[dict], max_age_days: int, today: date) -> list[str]:
    """Last-updated dates of the help pages this answer cites that are older than the limit."""
    if max_age_days <= 0:
        return []
    cited = {u.rstrip(".,;:") for u in _URL.findall(answer)}
    out = set()
    for c in tool_log:
        if c["name"] != "search_knowledge_base" or c["result"]["status"] != "found":
            continue
        for h in c["result"]["data"] or []:
            if not isinstance(h, dict) or h.get("source_url") not in cited:
                continue
            age = age_days(h.get("updated", ""), today)
            if age is not None and age > max_age_days:
                out.add(str(h["updated"])[:10])
    return sorted(out)


def audit_pages(pages: list[dict[str, Any]], max_age_days: int, today: date) -> list[dict[str, str]]:
    """Findings for a person to fix in the source pages. Nothing is changed here."""
    findings: list[dict[str, str]] = []
    for p in pages:
        title = str(p.get("title", p.get("page_id", "?")))
        age = age_days(str(p.get("updated", "")), today)
        if age is None:
            findings.append({"page": title, "kind": "no_date", "detail": "no usable 'updated' date"})
        elif max_age_days > 0 and age > max_age_days:
            findings.append({"page": title, "kind": "stale", "detail": f"last updated {age} days ago"})
        for heading, body in _SECTION.findall(str(p.get("html", ""))):
            m = _CODE_HEADING.match(_TAG.sub(" ", heading))
            code = m.group(1).upper() if m else ""
            text = _TAG.sub(" ", body)
            if code in POLICY and (r := _RETRYABLE.search(text)):
                says = r.group(1).lower() == "yes"
                if says != POLICY[code][0]:
                    findings.append({"page": title, "kind": "retry_conflict",
                                     "detail": f"{code}: page says retryable {r.group(1).lower()}, "
                                               f"policy table says {'yes' if POLICY[code][0] else 'no'}"})
            mm = _MEANING.search(text)
            if code in CANONICAL and mm and mm.group(1).strip().lower() != CANONICAL[code][1].lower():
                findings.append({"page": title, "kind": "meaning_conflict",
                                 "detail": f"{code}: page says '{mm.group(1).strip()}', "
                                           f"code table says '{CANONICAL[code][1]}'"})
    return findings
