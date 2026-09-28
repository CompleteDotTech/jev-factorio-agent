"""Fail-closed economic admission for a new optional coal network.

The current native route proves finite source/flow and paid placement, but its
electric network witness does not attribute generating fuel or bound other
loads. No positive payback conclusion is available from protocol 1 or 2.
"""
from __future__ import annotations

from .. import coal_supply


def evaluate(snapshot) -> dict:
    """Return decision-local evidence after validating the complete observation."""
    rows = coal_supply.sources(snapshot)
    data = snapshot.factory["coal_supply"]
    if data["protocol"] == 1:
        reason = "coal_admission_v2_unavailable"
    else:
        reason = data["admission"]["reason"]
    return {"eligible": False, "reason": reason, "observed_tick": snapshot.tick,
            "session_id": snapshot.session_id, "source_count": len(rows)}
