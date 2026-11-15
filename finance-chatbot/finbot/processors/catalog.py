"""Processor catalog: what each processor supports and costs, plus known incidents.

The catalog is configuration, loaded from `processors.json` in the data
directory when present (scripts/generate_synthetic.py writes one), else the
defaults below. Incidents are platform-wide facts (an outage window), not
tenant data, so every tenant may see them.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ProcessorProfile:
    name: str
    currencies: frozenset[str]
    brands: frozenset[str]
    local_countries: frozenset[str]   # where it acquires locally (better approval rates, lower fees)
    fee_bps: int                      # blended cost in basis points

    def supports(self, currency: str, brand: str) -> bool:
        return currency in self.currencies and brand in self.brands


@dataclass(frozen=True)
class Incident:
    processor: str
    start: str      # ISO 8601 UTC
    end: str
    kind: str       # "outage" | "degraded"
    summary: str

    def covers(self, at: str) -> bool:
        return _ts(self.start) <= _ts(at) < _ts(self.end)


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


DEFAULT_PROFILES = (
    ProcessorProfile("northstar", frozenset({"USD", "CAD"}), frozenset({"visa", "mastercard", "discover"}),
                     frozenset({"US", "CA"}), 210),
    ProcessorProfile("meridian", frozenset({"USD", "EUR", "GBP", "CAD"}), frozenset({"visa", "mastercard", "amex"}),
                     frozenset({"GB"}), 240),
    ProcessorProfile("keystone", frozenset({"USD", "EUR", "GBP"}),
                     frozenset({"visa", "mastercard", "amex", "discover"}), frozenset({"US"}), 260),
)


@dataclass
class ProcessorDirectory:
    profiles: dict[str, ProcessorProfile]
    incidents: list[Incident] = field(default_factory=list)

    @classmethod
    def default(cls) -> ProcessorDirectory:
        return cls({p.name: p for p in DEFAULT_PROFILES})

    @classmethod
    def load(cls, data_dir: str) -> ProcessorDirectory:
        path = os.path.join(data_dir, "processors.json") if data_dir else ""
        if not path or not os.path.exists(path):
            return cls.default()
        with open(path) as f:
            raw = json.load(f)
        profiles = {p["name"]: ProcessorProfile(p["name"], frozenset(p["currencies"]), frozenset(p["brands"]),
                                                frozenset(p["local_countries"]), int(p["fee_bps"]))
                    for p in raw["processors"]}
        return cls(profiles, [Incident(**i) for i in raw.get("incidents", [])])

    def names(self) -> list[str]:
        return list(self.profiles)

    def incidents_for(self, processor: str) -> list[Incident]:
        return [i for i in self.incidents if i.processor == processor]

    def to_json(self) -> dict[str, Any]:
        def profile(p: ProcessorProfile) -> dict[str, Any]:
            d = asdict(p)
            for k in ("currencies", "brands", "local_countries"):
                d[k] = sorted(d[k])
            return d
        return {"processors": [profile(p) for p in self.profiles.values()],
                "incidents": [asdict(i) for i in self.incidents]}
