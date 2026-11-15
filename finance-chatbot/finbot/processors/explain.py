"""Read side: what the support bot's routing tools return.

The bot never routes payments. It reads the RoutingTrace stored on the
transaction and the processor catalog, and gets code-computed advice:

  routing_view   the trace for one transaction plus `cascade_advice`
                 (could it have gone to another processor, and why or why not)
  health_view    a processor's incidents plus THIS tenant's recent attempt
                 stats on it. Other tenants' traffic never leaks into the numbers.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from .cascade import CascadePolicy
from .catalog import ProcessorDirectory
from .codes import PROCESSOR_FAULT_CODES, outcome_of


def routing_view(record: dict[str, Any]) -> dict[str, Any] | None:
    """The stored trace plus advice computed in code; None when the record has no trace."""
    trace = record.get("routing")
    if not isinstance(trace, dict):
        return None
    out: dict[str, Any] = {"txn_id": record["txn_id"], **trace}
    attempts = trace.get("attempts") or []
    if attempts:
        final = attempts[-1]
        decision = CascadePolicy.advice(outcome_of(final["code"]))
        out["cascade_advice"] = {"final_processor": final["processor"], "final_code": final["code"],
                                 "another_processor_would_help": decision.allowed, "reason": decision.reason}
    else:
        out["cascade_advice"] = {"another_processor_would_help": False,
                                 "reason": "Stopped before routing (" + str(trace.get("not_routed_reason", ""))
                                           + "); no processor was involved."}
    return out


def health_view(processor: str, directory: ProcessorDirectory,
                tenant_records: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    if processor not in directory.profiles:
        return None
    attempts = errors = 0
    last_error = ""
    for rec in tenant_records:
        for a in (rec.get("routing") or {}).get("attempts") or []:
            if a["processor"] != processor:
                continue
            attempts += 1
            if a["code"] in PROCESSOR_FAULT_CODES:
                errors += 1
                last_error = max(last_error, rec.get("created_at", ""))
    return {"processor": processor,
            "incidents": [asdict(i) for i in directory.incidents_for(processor)],
            "your_attempts": attempts, "your_processor_errors": errors,
            "your_error_rate": round(errors / attempts, 4) if attempts else None,
            "your_last_processor_error_at": last_error or None}
