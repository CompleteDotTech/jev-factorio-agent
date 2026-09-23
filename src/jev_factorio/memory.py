"""Atomic, session-bound controller checkpoints (not Factorio save files)."""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .planning.materials import quantities
from .telemetry import fingerprint, make_attempt, validate_attempt


@dataclass
class CampaignMemory:
    session_id: str
    target: str
    version: int = 2
    active_goal: str | None = None
    completed_goals: dict[str, int] = field(default_factory=dict)
    active_plan: dict | None = None
    step_index: int = 0
    pending: dict | None = None
    reservations: dict[str, dict[str, float]] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    last_tick: int = -1
    status: str = "running"
    reason: str = ""
    stalled_decisions: int = 0
    attempt: dict | None = None
    attempt_outcomes: list[dict] = field(default_factory=list)
    transfer_recovery: dict | None = None
    capital_investment: dict | None = None

    def event(self, kind: str, **details) -> None:
        self.history.append({"kind": kind, **details})
        self.history = self.history[-64:]

    def reserve(self, owner: str, costs: dict[str, float], inventory: dict[str, int]) -> None:
        costs = quantities(costs)
        available = quantities(inventory)
        for key, held in self.reservations.items():
            if key != owner:
                for item, amount in held.items():
                    available[item] = available.get(item, 0) - amount
        if any(amount > available.get(item, 0) for item, amount in costs.items()):
            raise ValueError("Insufficient unreserved construction materials")
        self.reservations[owner] = costs

    def release(self, owner: str) -> None:
        self.reservations.pop(owner, None)

    def save(self, path: Path | None) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                data = asdict(self)
                if self.capital_investment is None:
                    data.pop('capital_investment')
                json.dump(data, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path: Path, session_id: str, target: str) -> CampaignMemory:
        try:
            def invalid_constant(value):
                raise ValueError(f"Invalid numeric constant in checkpoint: {value}")

            data = json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid_constant)
            if not isinstance(data, dict) or type(data.get("version")) is not int:
                raise ValueError("Invalid checkpoint version")
            if data["version"] == 1 and {"attempt", "attempt_outcomes"} & data.keys():
                raise ValueError("Legacy checkpoint has unexpected attempt fields")
            if data["version"] == 2 and not {"attempt", "attempt_outcomes"} <= data.keys():
                raise ValueError("Version 2 checkpoint is missing attempt fields")
            memory = cls(**data)
            if memory.transfer_recovery is not None and not isinstance(memory.transfer_recovery, dict):
                raise ValueError("Invalid transfer recovery reference")
            if memory.transfer_recovery is not None and memory.pending is None:
                raise ValueError("Transfer recovery requires a retained pending action")
            if (memory.version not in {1, 2} or memory.session_id != session_id
                    or memory.target != target or not session_id):
                raise ValueError("Checkpoint version, session, or target mismatch")
            if (type(memory.last_tick) is not int or type(memory.step_index) is not int
                    or memory.step_index < 0 or not isinstance(memory.history, list)
                    or not isinstance(memory.completed_goals, dict)
                    or not isinstance(memory.failures, dict)
                    or memory.status not in {"running", "completed", "blocked", "uncertain"}):
                raise ValueError("Invalid checkpoint state")
            from .planning.goals import goal_order
            order = goal_order(target)
            if (memory.last_tick < -1 or memory.active_goal not in [None, *order]
                    or not set(memory.completed_goals).issubset(order)
                    or any(type(t) is not int or not 0 <= t <= memory.last_tick
                           for t in memory.completed_goals.values())
                    or any(type(n) is not int or n < 0 for n in memory.failures.values())
                    or type(memory.stalled_decisions) is not int or memory.stalled_decisions < 0
                    or len(memory.history) > 64 or not all(isinstance(e, dict) for e in memory.history)):
                raise ValueError("Invalid checkpoint receipts or counters")
            if memory.capital_investment is not None:
                from .planning.capital import MARKER, matches, validate_state
                validate_state(memory.capital_investment, memory.last_tick)
                if memory.target != 'rocket_launch' or memory.active_goal != 'rocket_launch':
                    raise ValueError('Capital investment requires the rocket production goal')
                if memory.active_plan and MARKER in (memory.active_plan.get('materials') or {}):
                    from .skills import Plan
                    if not matches(Plan.from_dict(memory.active_plan), memory.capital_investment):
                        raise ValueError('Active plan and capital investment disagree')
            for costs in memory.reservations.values():
                quantities(costs)
            if memory.active_plan is not None:
                # Import locally to avoid a memory/skill dependency cycle.
                from .skills import Plan
                plan = Plan.from_dict(memory.active_plan)
                if plan.goal != memory.active_goal or memory.step_index >= len(plan.steps):
                    raise ValueError("Checkpoint step outside plan")
            if memory.pending is not None and memory.active_plan is None:
                raise ValueError("Pending action without an active plan")
            if memory.pending is not None:
                pending = memory.pending
                if (not isinstance(pending, dict)
                        or set(pending) != {"started_tick", "polls", "action", "dispatch"}
                        or type(pending["started_tick"]) is not int
                        or not 0 <= pending["started_tick"] <= memory.last_tick
                        or type(pending["polls"]) is not int or pending["polls"] < 0
                        or pending["action"] != plan.steps[memory.step_index].action
                        or pending["dispatch"] not in {"prepared", "ambiguous", "returned"}):
                    raise ValueError("Invalid pending action in checkpoint")
            # Migration changes metadata only, never the pending command or receipts.
            # Loading (including offline diagnostics) does not write the source file.
            if memory.version == 1:
                memory.version = 2
                if memory.pending:
                    memory.attempt = make_attempt(session_id, target, memory.active_plan,
                                                  memory.step_index, memory.pending)
            if (memory.pending is None) != (memory.attempt is None):
                raise ValueError("Pending action and attempt identity must coexist")
            if memory.attempt is not None:
                validate_attempt(memory.attempt)
                attempt = memory.attempt
                step = memory.active_plan["steps"][memory.step_index]
                if (attempt["action"] != memory.pending["action"]
                        or attempt["started_tick"] != memory.pending["started_tick"]
                        or attempt["plan_id"] != plan.id or attempt["step_index"] != memory.step_index
                        or attempt["step_sha256"] != fingerprint(step)
                        or attempt["receipt"] != (step.get("parameters") or {}).get("receipt")):
                    raise ValueError("Attempt does not match the pending operation")
            if not isinstance(memory.attempt_outcomes, list) or len(memory.attempt_outcomes) > 64:
                raise ValueError("Invalid attempt outcome history")
            seen = {memory.attempt["id"]} if memory.attempt else set()
            for outcome in memory.attempt_outcomes:
                validate_attempt(outcome, finished=True)
                if outcome["id"] in seen or outcome["finished_tick"] > memory.last_tick:
                    raise ValueError("Duplicate or future attempt outcome")
                seen.add(outcome["id"])
            return memory
        except (TypeError, KeyError, AttributeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid controller checkpoint; refusing to reset it") from error


def load_checkpoint(path: Path, session_id: str, target: str) -> CampaignMemory:
    from .controller import HierarchicalLoop

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid controller checkpoint")
    loop_type = HierarchicalLoop
    if {"background_schema", "background_job", "background_attempt"} & data.keys():
        if not {"background_schema", "background_job"} <= data.keys():
            raise ValueError("Incomplete background checkpoint extension")
        from .background import BackgroundWorkLoop
        loop_type = BackgroundWorkLoop
    if {"input_routes_schema", "input_commitments"} & data.keys():
        if not {"input_routes_schema", "input_commitments"} <= data.keys():
            raise ValueError("Incomplete input-route checkpoint extension")
        from .input_controller import input_loop_type
        loop_type = input_loop_type(loop_type)
    if {'outposts_schema', 'outpost_commitments'} & data.keys():
        if not {'outposts_schema', 'outpost_commitments', 'input_routes_schema', 'input_commitments'} <= data.keys():
            raise ValueError('Incomplete mining-outpost checkpoint extension')
        from .outpost_controller import outpost_loop_type
        loop_type = outpost_loop_type(loop_type)
    return loop_type.memory_type.load(path, session_id, target)
