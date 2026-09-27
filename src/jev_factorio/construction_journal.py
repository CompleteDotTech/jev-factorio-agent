"""Bind native construction journals to the existing durable controller attempt.

A native prepared receipt is a recovery fact, not permission to create a new plan.
This check neither adopts a journal nor mutates either side of the binding. The
normal pending verifier still owns bounded replay and paid-receipt verification.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Mapping

from .skills import Plan
from .telemetry import fingerprint

if TYPE_CHECKING:
    from .memory import CampaignMemory


def require_owner(memory: CampaignMemory, *, action: str, binding: Mapping[str, str],
                  layout: str, journal: dict) -> None:
    """Reject any present journal not owned by this exact retained operation.

    Call only after validating the native row. ``binding`` is the exact route or
    consumer identifier from that row, not a replaceable project/failure key.
    Check the attempt as well as the plan: merely selecting identical parameters
    before the write-ahead pending save has not established a recovery owner.
    """
    if not journal:
        return
    pending, attempt = memory.pending, memory.attempt
    if (not isinstance(pending, dict) or not isinstance(attempt, dict)
            or pending.get('action') != action
            or pending.get('dispatch') not in {'prepared', 'returned', 'ambiguous'}
            or not isinstance(memory.active_plan, dict)
            or type(memory.step_index) is not int or memory.step_index < 0):
        raise ValueError('Native construction journal has no durable controller owner')
    plan = Plan.from_dict(memory.active_plan)
    if memory.step_index >= len(plan.steps):
        raise ValueError('Native construction journal owner has no active step')
    step = plan.steps[memory.step_index]
    parameters = step.parameters or {}
    expected = {**binding, 'layout': layout, 'part': journal['part'],
                'receipt': journal['receipt']}
    if (step.action != action or parameters != expected
            or attempt.get('action') != action or attempt.get('plan_id') != plan.id
            or attempt.get('step_index') != memory.step_index
            or attempt.get('started_tick') != pending.get('started_tick')
            or attempt.get('receipt') != journal['receipt']
            or attempt.get('step_sha256') != fingerprint(memory.active_plan['steps'][memory.step_index])):
        raise ValueError('Native construction journal differs from its durable controller owner')
