"""Opt-in hierarchical control: commit, execute, observe, verify, and recover.

The inherited run loop retains the existing monotonic deadline and transient
HTTP-failure handling. No live-game or model-performance claims follow from
passing the offline tests.
"""
from __future__ import annotations

import json
import math
import time
from copy import deepcopy
from uuid import uuid4
from dataclasses import asdict, replace
from pathlib import Path

from .causal_trace import CausalTrace, traced_step
from .research_log import EventSink, ResearchLogError, validate_output_paths
from .judgments import Decision, select_plan
from .loop import AgentLoop
from .memory import CampaignMemory
from .planning.goals import GOALS, completed, goal_order
from .skills import Plan, compile_plans
from .state import GameSnapshot
from .provenance import gameplay_context
from .telemetry import DISPATCH_STAGES, error_code, make_attempt, phase, utc_now, validate_phase


def _json_safe(value):
    """Keep malformed numeric answers auditable without emitting invalid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_numeric": repr(value)}
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class HierarchicalLoop(AgentLoop):
    memory_type = CampaignMemory

    def __init__(self, backend, jev=None, *, target: str = "rocket_launch",
                 policy: str = "jev", checkpoint: str | None = None,
                 resume_controller: bool = False, confidence_floor: float = 0.45,
                 tick_seconds: float = 2.0, log_file: str | None = None,
                 max_request_bytes: int = 32000, max_pending_polls: int = 32,
                 max_stalled_decisions: int = 4, factory_scheduling: str = "serial",
                 research_log: EventSink | None = None):
        if factory_scheduling not in {"serial", "ready-work"}:
            raise ValueError("Unknown factory scheduling policy")
        self.factory_scheduling = factory_scheduling
        if policy not in {"jev", "deterministic", "hybrid"}:
            raise ValueError("Unknown campaign policy")
        if policy != "deterministic" and jev is None:
            raise ValueError("Supply an explicit Jev client; mock use must be intentional")
        if not math.isfinite(confidence_floor) or not 0 <= confidence_floor <= 1:
            raise ValueError("Confidence floor must be in [0, 1]")
        if not math.isfinite(tick_seconds) or tick_seconds < 0:
            raise ValueError("Invalid decision interval")
        if min(max_request_bytes, max_pending_polls, max_stalled_decisions) < 1:
            raise ValueError("Controller budgets must be positive")
        validate_output_paths(research_log, log_file, checkpoint)
        self.provenance = gameplay_context()
        self.order = goal_order(target)
        self.backend, self.jev, self.policy = backend, jev, policy
        self.target, self.confidence_floor = target, confidence_floor
        self.tick_seconds = tick_seconds
        self.log_file = Path(log_file) if log_file else None
        self.checkpoint = Path(checkpoint) if checkpoint else None
        self.resume_controller = resume_controller
        if resume_controller and (self.checkpoint is None or not self.checkpoint.is_file()):
            raise ValueError("Resuming requires an existing controller checkpoint")
        if self.checkpoint and self.checkpoint.exists() and not resume_controller:
            raise ValueError("Checkpoint exists; explicitly resume or use a new path")
        self.max_request_bytes = max_request_bytes
        self.max_pending_polls = max_pending_polls
        self.max_stalled_decisions = max_stalled_decisions
        self.memory: CampaignMemory | None = None
        self._decision: Decision | None = None
        self._process_id = uuid4().hex
        self._attempt_clock: tuple[str, float] | None = None
        self._phases: list[dict] = []
        self.catalog = None
        self._trace = CausalTrace(research_log, "hierarchical", jev, provenance=self.provenance)
        if target in {"rocket_launch", "iron_smelting", "steam_power", "automation_science"} \
                and hasattr(backend, "enable_factory"):
            self.catalog = backend.enable_factory()
            self.max_pending_polls = max(self.max_pending_polls, 1800)

    @property
    def terminal(self) -> bool:
        return self.memory is not None and self.memory.status in {"completed", "blocked", "uncertain"}

    def _diagnostic_trace(self, event: dict) -> None:
        validate_phase(event)
        self._phases.append(deepcopy(event))
        self._phases = self._phases[-64:]
        if self.memory is not None and self.memory.attempt is not None:
            if event["stage"] in DISPATCH_STAGES:
                self.memory.attempt["dispatch_phases"][event["stage"]] = deepcopy(event)
                self._save()
            elif event["status"] == "failed":
                self.memory.attempt["observation_error"] = deepcopy(event)
                self._save()

    def _finish_attempt(self, snapshot: GameSnapshot, outcome: str = "verified") -> None:
        attempt = self.memory.attempt
        if attempt is None:
            raise ValueError("Cannot finish an unidentified pending action")
        latency = None
        if self._attempt_clock is not None and self._attempt_clock[0] == attempt["id"]:
            latency = time.perf_counter() - self._attempt_clock[1]
        self.memory.attempt_outcomes.append({
            **deepcopy(attempt), "outcome": outcome, "finished_tick": snapshot.tick,
            "finished_at_utc": utc_now(), "latency_seconds": latency,
        })
        self.memory.attempt_outcomes = self.memory.attempt_outcomes[-64:]
        self._trace.release_attempt(attempt["id"])
        self.memory.attempt = None
        self._attempt_clock = None

    def _observe(self, stage: str = "observe") -> GameSnapshot:
        with phase(stage, self._diagnostic_trace):
            return self._observe_snapshot()

    def _observe_snapshot(self) -> GameSnapshot:
        snapshot = self._trace.observe(self.backend, self._trace.observation_phase)
        if not snapshot.session_id or snapshot.world_kind not in {"mock", "fle"}:
            raise ValueError("Hierarchical control requires identified backend/session telemetry")
        if self.policy != "deterministic" and getattr(self.jev, "is_mock", False) and snapshot.world_kind != "mock":
            raise ValueError("A mock model cannot control or benchmark a live backend")
        if type(snapshot.tick) is not int or snapshot.tick < 0:
            raise ValueError("Invalid observation tick")
        # Validate serialized facts rather than allowing NaN into conditions.
        json.dumps(snapshot.for_jev(), allow_nan=False)
        if self.memory is None:
            self.memory = (self.memory_type.load(self.checkpoint, snapshot.session_id, self.target)
                           if self.resume_controller else self.memory_type(snapshot.session_id, self.target))
        if self.memory.session_id != snapshot.session_id or snapshot.tick < self.memory.last_tick:
            raise ValueError("Session changed or observation tick regressed; refusing to act")
        self.memory.last_tick = snapshot.tick
        self._trace.emit("observation_validated", {"accepted": True})
        return snapshot

    def _save(self) -> None:
        self._trace.call("checkpoint_written", lambda: self.memory.save(self.checkpoint),
                         details={"persisted": self.checkpoint is not None})

    def _clear_plan(self) -> None:
        attempt = self.memory.attempt
        background = getattr(self.memory, "background_attempt", None)
        if attempt is not None and (background is None or background["id"] != attempt["id"]):
            self._trace.release_attempt(attempt["id"])
        if self.memory.active_plan:
            self.memory.release(self.memory.active_plan["id"])
        self.memory.active_plan = None
        self.memory.pending = None
        self.memory.attempt = None
        self._attempt_clock = None
        self.memory.step_index = 0
        self._trace.clear_pending()

    def _fail_plan(self, reason: str) -> None:
        key = self.memory.active_plan["id"]
        self.memory.failures[key] = self.memory.failures.get(key, 0) + 1
        self.memory.event("plan_failed", plan=key, reason=reason, tick=self.memory.last_tick)
        self._trace.emit("plan_failed", {"plan_id": key, "reason": reason})
        self.memory.reason = reason
        self._clear_plan()
        self._save()

    def _refresh_goals(self, snapshot: GameSnapshot) -> None:
        for key in self.order:
            if key not in self.memory.completed_goals and all(
                dep in self.memory.completed_goals for dep in GOALS[key].prerequisites
            ) and self._trace.call("goal_checked", lambda: completed(key, snapshot),
                                   details={"goal": key}, result=lambda value: {"completed": value}):
                self.memory.completed_goals[key] = snapshot.tick
                self.memory.event("goal_completed", goal=key, tick=snapshot.tick,
                                  world_kind=snapshot.world_kind)
                self._trace.emit("goal_completed", {"goal": key, "tick": snapshot.tick,
                                                    "verification_source": "existing_goal_predicate"})
        if self.target in self.memory.completed_goals:
            self.memory.status, self.memory.reason = "completed", "Verified target milestone"
            self._clear_plan()
            return
        goal = next(key for key in self.order if key not in self.memory.completed_goals)
        if self.memory.active_goal != goal:
            self._clear_plan()
            self.memory.active_goal = goal
            self.memory.event("goal_activated", goal=goal, tick=snapshot.tick)
            self._trace.emit("goal_activated", {"goal": goal})

    def _trace_decision(self) -> None:
        if self._trace.enabled:
            decision = self._decision
            self._trace.emit("decision", {"plan_id": decision.plan_id, "source": decision.source,
                                          "reason": decision.reason, "utilities": decision.utilities,
                                          "model_called": decision.model_called, "policy": self.policy,
                                          "confidence_floor": self.confidence_floor,
                                          "diagnostics": decision.diagnostics,
                                          "selection_support": getattr(self, "_selection_support", {})})

    def _record(self, before: GameSnapshot, action: str, outcome: str,
                after: GameSnapshot | None = None, verified: bool = False) -> dict:
        self._save()
        decision = self._decision
        record = {
            **self.provenance,
            "schema_version": 2, "controller": "hierarchical", "policy": self.policy,
            "tick": before.tick, "session_id": before.session_id,
            "world_kind": before.world_kind, "goal": self.memory.active_goal,
            "target": self.target, "status": self.memory.status, "reason": self.memory.reason,
            "action": action, "outcome": outcome, "verified": verified,
            "state": before.for_jev(), "after_state": (after or before).for_jev(),
            "completed_goals": dict(self.memory.completed_goals),
            "decision": asdict(decision) if decision else None,
            "model_call": decision is not None and decision.model_called,
            "requested_model": getattr(self.jev, "model", None),
            "resolved_model": getattr(self.jev, "last_model", None) if decision and decision.model_called else None,
            "usage": getattr(self.jev, "last_usage", None) if decision and decision.model_called else None,
            "pending": deepcopy(self.memory.pending), "history": deepcopy(self.memory.history[-8:]),
            "process_id": self._process_id, "recorded_at_utc": utc_now(),
            "phases": deepcopy(self._phases), "attempt": deepcopy(self.memory.attempt),
            "attempt_outcomes": deepcopy(self.memory.attempt_outcomes[-8:]),
        }
        if getattr(self, "factory_scheduling", "serial") != "serial":
            record["factory_scheduling"] = self.factory_scheduling
        fair = getattr(getattr(self, "backend", None), "_fair", None)
        metrics = getattr(fair, "metrics", None)
        if isinstance(metrics, dict):
            record["fair_action_metrics"] = dict(metrics)
        record.update(self._record_extras())
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(_json_safe(record), allow_nan=False) + "\n")
        print(f"[t={before.tick}] {self.memory.status}: {action} -> {outcome}", flush=True)
        return record

    def _model_facts(self, snapshot: GameSnapshot) -> dict:
        return snapshot.for_jev()

    def _record_extras(self) -> dict:
        return {}

    def _step_allowed(self, step, snapshot: GameSnapshot) -> bool:
        return step.allowed(snapshot)

    def _execution_barrier(self, snapshot: GameSnapshot) -> bool:
        return False

    def _absent_ambiguous_placement(self, plan: Plan, step, snapshot: GameSnapshot) -> bool:
        """Prove that retrying an ambiguous placement cannot duplicate a building."""
        pending = self.memory.pending or {}
        if pending.get("dispatch") != "ambiguous" or step.action != "factory_place":
            return False
        parameters = step.parameters or {}
        role, name = parameters.get("role"), parameters.get("name")
        entities = snapshot.factory.get("entities", {})
        counts = snapshot.factory.get("force_entity_counts")
        costs = step.costs or {}
        reserved = self.memory.reservations.get(plan.id)
        return bool(
            role and name and role not in entities
            and isinstance(counts, dict) and counts.get(name, 0) == 0
            and costs and reserved == costs
            and all(snapshot.inventory.get(item, 0) >= quantity
                    for item, quantity in costs.items())
        )

    def _absent_ambiguous_connection(self, plan: Plan, step, snapshot: GameSnapshot) -> bool:
        """Prove an ambiguous connection placed no connector before permitting a replan."""
        pending = self.memory.pending or {}
        if pending.get("dispatch") != "ambiguous" or step.action != "factory_connect":
            return False
        parameters = step.parameters or {}
        source, target, kind = (
            parameters.get("source"), parameters.get("target"), parameters.get("kind")
        )
        entities = snapshot.factory.get("entities", {})
        counts = snapshot.factory.get("force_entity_counts")
        costs = step.costs or {}
        reserved = self.memory.reservations.get(plan.id)
        count = counts.get(kind, 0) if isinstance(counts, dict) else None
        retained = all(snapshot.inventory.get(item, 0) >= quantity
                       for item, quantity in costs.items())
        return bool(
            source in entities and target in entities and kind in {"pipe", "small-electric-pole"}
            and isinstance(counts, dict)
            and type(count) is int and count >= 0
            and set(costs) == {kind} and reserved == costs
            and retained and count == 0
        )

    def _partial_unacknowledged_gather(self, step, snapshot: GameSnapshot) -> bool:
        """Identify a partial native gather without treating it as step success.

        An inventory increase below the committed threshold is not a license to
        replay an unacknowledged harvest.  The resumed controller instead fails
        this plan and replans from the observed inventory.  This is deliberately
        narrower than normal inventory verification: it applies only after an
        prepared or ambiguous factory gather has already been observed at least
        once.
        """
        pending = self.memory.pending or {}
        parameters = step.parameters or {}
        item = parameters.get("resource")
        requested = parameters.get("quantity")
        observed = snapshot.inventory.get(item, 0) if isinstance(item, str) else 0
        return bool(
            pending.get("dispatch") in {"prepared", "ambiguous"}
            and type(pending.get("polls")) is int and pending["polls"] > 0
            and step.action == "factory_gather" and step.effect == "inventory"
            and item == step.item and item
            and type(requested) is int and requested > 0
            and isinstance(observed, (int, float)) and not isinstance(observed, bool)
            and math.isfinite(observed)
            and 0 <= step.threshold - requested < observed < step.threshold
        )

    def _verify_pending(self, snapshot: GameSnapshot) -> dict:
        plan = Plan.from_dict(self.memory.active_plan)
        step = plan.steps[self.memory.step_index]
        pending = self.memory.pending
        with phase("verification", self._diagnostic_trace):
            verified = self._trace.verify(step, snapshot, plan_id=plan.id,
                                          index=self.memory.step_index, pending=pending,
                                          phase="pending_poll", attempt_id=self.memory.attempt["id"])
        if verified:
            self._finish_attempt(snapshot)
            self.memory.status, self.memory.reason = "running", ""
            self.memory.release(plan.id)
            self.memory.pending = None
            self._trace.clear_pending()
            self.memory.step_index += 1
            self.memory.stalled_decisions = 0
            self.memory.event("step_verified", plan=plan.id, action=step.action, tick=snapshot.tick)
            if self.memory.step_index == len(plan.steps):
                self._clear_plan()
            self._refresh_goals(snapshot)
            return self._record(snapshot, "verify", "Observed expected postcondition", verified=True)
        if self._absent_ambiguous_placement(plan, step, snapshot):
            name = step.parameters["name"]
            reason = (f"Observed no durable {name} placement and retained all reserved "
                      "materials; replan without replaying the ambiguous dispatch")
            self.memory.status = "running"
            self._fail_plan(reason)
            return self._record(snapshot, "reconcile", reason)
        if self._absent_ambiguous_connection(plan, step, snapshot):
            kind = step.parameters["kind"]
            reason = (f"Observed no durable {kind} construction and retained all reserved "
                      "materials; replan without replaying the ambiguous dispatch")
            self.memory.status = "running"
            self._fail_plan(reason)
            return self._record(snapshot, "reconcile", reason)
        if self._partial_unacknowledged_gather(step, snapshot):
            quantity = snapshot.inventory[step.item]
            self.memory.event(
                "gather_partial_progress", session_id=snapshot.session_id,
                plan=plan.to_dict(), step_index=self.memory.step_index,
                observed_inventory=quantity, tick=snapshot.tick,
                started_tick=pending["started_tick"], attempt_id=self.memory.attempt["id"],
            )
            reason = (
                f"Observed {quantity} {step.item} below committed inventory threshold "
                f"{step.threshold} after an unacknowledged native gather; replan without "
                "replaying the ambiguous dispatch"
            )
            self.memory.status = "running"
            self._fail_plan(reason)
            return self._record(snapshot, "reconcile", reason)
        boiler = snapshot.factory.get("entities", {}).get("utility:boiler", {})
        if step.action == "factory_wait" and boiler and boiler.get("fuel", {}).get("coal", 0) < 5:
            self._finish_attempt(snapshot, "wait_replanned")
            self.memory.event("maintenance_required", reason="boiler fuel", tick=snapshot.tick)
            self._clear_plan()
            return self._record(snapshot, "observe", "Replan a nonmutating wait to replenish boiler fuel")
        if (self.factory_scheduling == "ready-work" and self.catalog is not None
                and self.memory.status == "running" and pending.get("dispatch") == "returned"
                and step.action == "factory_wait" and step.effect == "machine_output"):
            candidates, _ = self._compile_candidates(snapshot)
            ready = [candidate for candidate in candidates
                     if self.memory.failures.get(candidate.id, 0) < 2
                     and candidate.steps[0].action != "factory_wait"
                     and candidate.steps[0].allowed(snapshot)
                     and not candidate.steps[0].satisfied(snapshot)]
            if ready:
                self._finish_attempt(snapshot, "wait_replanned")
                self.memory.event("passive_wait_yielded", plan=plan.id,
                                  candidates=[candidate.id for candidate in ready], tick=snapshot.tick)
                self._clear_plan()
                return self._record(snapshot, "observe", "Yield passive machine wait to ready work")
        pending["polls"] += 1
        expired = (snapshot.tick - pending["started_tick"] >= step.timeout_ticks
                   or pending["polls"] >= self.max_pending_polls)
        if expired:
            if self._trace.enabled:
                self._trace.emit("pending_expired", {
                    **self._trace.pending_ref(plan.id, self.memory.step_index, pending,
                                              attempt_id=self.memory.attempt["id"]),
                    "polls": pending["polls"], "timeout_ticks": step.timeout_ticks})
            if step.action in {"idle", "factory_wait"}:
                self._finish_attempt(snapshot, "wait_expired")
                self._fail_plan("Production made no verified progress within the observation budget")
                return self._record(snapshot, "observe", self.memory.reason)
            # Execution may have partially mutated the game. Do not automatically
            # replay a non-idempotent command after lost acknowledgement.
            self.memory.status = "uncertain"
            self.memory.reason = "Unverified action outcome; inspect/reconcile before another mutation"
            return self._record(snapshot, "observe", self.memory.reason)
        # In a real backend observation allows game time to elapse naturally.
        # The explicitly synthetic backend advances only when given idle.
        if snapshot.world_kind == "mock":
            self._trace.dispatch(lambda: self.backend.act("idle"), "idle",
                                 plan_id=plan.id, step_index=self.memory.step_index,
                                 role="mock_clock_advance")
        return self._record(snapshot, "observe", "Waiting for the in-flight postcondition")

    def _gather_remainder_plan(self, plan: Plan, snapshot: GameSnapshot) -> Plan:
        if len(plan.steps) != 1 or plan.steps[0].action != "factory_gather":
            return plan
        step = plan.steps[0]
        requested = (step.parameters or {}).get("quantity")
        observed = snapshot.inventory.get(step.item)
        if (type(requested) is not int or requested <= 0
                or type(observed) is not int or observed < 0
                or requested != step.threshold - observed):
            return plan
        for receipt in reversed(self.memory.history):
            if receipt.get("kind") != "gather_partial_progress":
                continue
            try:
                original = Plan.from_dict(receipt["plan"])
                previous = original.steps[0]
                quantity = (previous.parameters or {}).get("quantity")
                valid = (
                    receipt.get("session_id") == snapshot.session_id == self.memory.session_id
                    and len(original.steps) == 1 and type(receipt.get("step_index")) is int
                    and receipt["step_index"] == 0
                    and type(receipt.get("tick")) is int
                    and type(receipt.get("started_tick")) is int
                    and 0 <= receipt["started_tick"] <= receipt["tick"] <= snapshot.tick
                    and isinstance(receipt.get("attempt_id"), str) and bool(receipt["attempt_id"])
                    and type(receipt.get("observed_inventory")) is int
                    and receipt["observed_inventory"] == observed
                    and original.goal == plan.goal
                    and previous.action == step.action and previous.effect == step.effect == "inventory"
                    and previous.item == step.item
                    and previous.threshold == step.threshold
                    and type(quantity) is int and requested < quantity
                    and 0 <= previous.threshold - quantity < observed < previous.threshold
                    and (previous.parameters or {}).get("resource") == step.item
                    and original.id in {plan.id, f"{plan.id}:remainder-quantity:{quantity}"}
                )
            except (KeyError, TypeError, ValueError, AttributeError, IndexError):
                continue
            if valid:
                return replace(plan, id=f"{plan.id}:remainder-quantity:{requested}")
        return plan

    def _compile_candidates(self, snapshot: GameSnapshot) -> tuple[list[Plan], str]:
        if self.catalog is not None and self.memory.active_goal in {
            "rocket_launch", "iron_smelting", "steam_power", "automation_science", "bootstrap_mining"
        }:
            if self.factory_scheduling == "ready-work":
                from .planning.ready_work import compile_ready_factory

                plans, blocker = compile_ready_factory(self.memory.active_goal, snapshot, self.catalog)
            else:
                from .planning.factory import compile_factory

                plans, blocker = compile_factory(self.memory.active_goal, snapshot, self.catalog)
        else:
            plans, blocker = compile_plans(self.memory.active_goal, snapshot)
        return [self._gather_remainder_plan(plan, snapshot) for plan in plans], blocker

    def _fallback_plan(self, plans: list[Plan]) -> Plan:
        if self.factory_scheduling == "ready-work" and self.catalog is not None:
            evidence = getattr(self, "_selection_support", {})
            ranking = evidence.get("deterministic_ranking", [])
            by_id = {plan.id: plan for plan in plans}
            return next((by_id[key] for key in ranking if key in by_id), plans[0])
        return min(plans, key=lambda plan: (len(plan.steps), plan.id))

    @traced_step
    def step(self) -> dict:
        self._decision = None
        self._selection_support = {}
        self._trace.observation_phase = "before_decision"
        self._phases = []
        snapshot = self._observe()
        if self.memory.status == "uncertain" and self.memory.pending:
            return self._verify_pending(snapshot)
        if self.terminal:
            return self._record(snapshot, "observe", self.memory.reason)
        # Resolve in-flight work before processing model requests or goal changes.
        if self.memory.pending:
            return self._verify_pending(snapshot)
        self._refresh_goals(snapshot)
        if self.terminal:
            return self._record(snapshot, "observe", self.memory.reason, verified=True)
        if self.memory.active_plan is None:
            with phase("planning", self._diagnostic_trace):
                plans, blocker = self._trace.call(
                    "candidate_set_created", lambda: self._compile_candidates(snapshot),
                    result=lambda value: {"plans": [plan.to_dict() for plan in value[0]],
                                          "blocker": value[1]})
            plans = [p for p in plans if self.memory.failures.get(p.id, 0) < 2]
            if self._trace.enabled:
                self._trace.emit("candidate_set_filtered", {"eligible_plan_ids": [p.id for p in plans],
                                                           "filter": "existing_plan_failure_budget"})
            if not plans:
                self.memory.status, self.memory.reason = "blocked", blocker or "Plan failure budget exhausted"
                return self._record(snapshot, "observe", self.memory.reason)
            if self.factory_scheduling == "ready-work" and self.catalog is not None:
                from .planning.decision_support import distinct_candidates, scheduling_context

                plans = distinct_candidates(plans)
                self._selection_support = scheduling_context(
                    snapshot, self.catalog, plans, self.memory.active_goal)
                by_id = {plan.id: plan for plan in plans}
                plans = [by_id[key] for key in self._selection_support["deterministic_ranking"]]
            singleton = bool(self._selection_support and len(plans) == 1 and self.policy == "hybrid")
            if self.policy == "deterministic" or singleton:
                with phase("selection", self._diagnostic_trace):
                    chosen = self._fallback_plan(plans)
                self._decision = Decision(
                    chosen.id, "deterministic-singleton" if singleton else "deterministic",
                    "Only one distinct feasible continuation" if singleton else "",
                    state=self._selection_support,
                    diagnostics={"schema": 1, "outcome": "singleton" if singleton else "deterministic",
                                 "model_skipped": True})
                self._trace_decision()
            else:
                facts = self._model_facts(snapshot)
                if facts["factory"]:
                    receipts = facts["factory"].pop("receipts", {})
                    facts["factory"].pop("connectors", None)
                    facts["factory"]["native_transfer_receipt_count"] = len(receipts)
                state = {"facts": facts, "active_goal": asdict(GOALS[self.memory.active_goal]),
                         "history": self.memory.history[-8:], **self._selection_support}
                if self.factory_scheduling == "ready-work":
                    state["production_scheduling"] = {
                        "objective": "Advance the next production batch identified in plan descriptions",
                        "guidance": "Prefer useful work while machines run; avoid tiny pickups and idle waits",
                        "ultimate_goal": self.memory.active_goal,
                    }
                try:
                    with phase("selection", self._diagnostic_trace):
                        self._decision = select_plan(self._trace.client(self.jev), state, plans, self.confidence_floor,
                                                     self.max_request_bytes)
                except ValueError as error:
                    self._decision = Decision(None, "observe", str(error), state=state,
                                              diagnostics={"schema": 1, "outcome": "request_rejected"})
                chosen = next((p for p in plans if p.id == self._decision.plan_id), None)
                if chosen is None and self.policy == "hybrid":
                    chosen = self._fallback_plan(plans)
                    self._decision.plan_id = chosen.id
                    self._decision.source = "deterministic-fallback"
                self._trace_decision()
                if chosen is None:
                    self.memory.stalled_decisions += 1
                    self.memory.reason = self._decision.reason
                    if self.memory.stalled_decisions >= self.max_stalled_decisions:
                        self.memory.status = "blocked"
                    return self._record(snapshot, "observe", self.memory.reason)
            self.memory.active_plan = chosen.to_dict()
            self.memory.step_index = 0
            self.memory.event("plan_committed", plan=chosen.id, source=self._decision.source,
                              tick=snapshot.tick)
            self._save()
            if self._trace.enabled:
                self._trace.emit("plan_committed", {"plan_id": chosen.id, "plan": chosen.to_dict(),
                                                    "source": self._decision.source})

        # The world can change while a remote model evaluates the old snapshot.
        self._trace.observation_phase = "before_dispatch"
        fresh = self._observe("pre_dispatch_observe")
        if self._execution_barrier(fresh):
            return self._record(snapshot, "observe", self.memory.reason, fresh)
        plan = Plan.from_dict(self.memory.active_plan)
        index = self._trace.call(
            "plan_progress", lambda: plan.next_step(fresh, self.memory.step_index),
            details={"plan_id": plan.id, "from_index": self.memory.step_index},
            result=lambda value: {"next_step": value})
        if index == len(plan.steps):
            self._trace.emit("verification", {"phase": "existing_plan_effects", "scope": "plan",
                                              "plan_id": plan.id, "verified": True,
                                              "action_id": None})
            self._clear_plan()
            self._refresh_goals(fresh)
            return self._record(snapshot, "verify", "Plan effects already observed", fresh, True)
        self.memory.step_index = index
        step = plan.steps[index]
        if not self._trace.call("precondition_checked", lambda: self._step_allowed(step, fresh),
                                details={"plan_id": plan.id, "step_index": index},
                                result=lambda value: {"allowed": value}):
            self._fail_plan("Plan precondition changed; replan from current observations")
            return self._record(snapshot, "observe", self.memory.reason, fresh)
        try:
            self.memory.reserve(plan.id, step.costs or {}, fresh.inventory)
        except ValueError as error:
            self._fail_plan(str(error))
            return self._record(snapshot, "observe", str(error), fresh)
        self.memory.pending = {"started_tick": fresh.tick, "polls": 0,
                               "action": step.action, "dispatch": "prepared"}
        unit = fresh.factory.get("entities", {}).get((step.parameters or {}).get("role"), {}).get("unit_number")
        self.memory.attempt = make_attempt(
            fresh.session_id, self.target, self.memory.active_plan, index, self.memory.pending,
            process_id=self._process_id,
            unit_number=unit if type(unit) is int and unit > 0 else None,
        )
        self._attempt_clock = (self.memory.attempt["id"], time.perf_counter())
        # Write-ahead checkpoint: after a crash even a prepared command is
        # treated as potentially dispatched, never blindly replayed.
        self._save()
        try:
            def dispatch():
                if step.action.startswith("factory_"):
                    traced = getattr(self.backend, "execute_traced", None)
                    return (traced(step.action, step.parameters or {}, self._diagnostic_trace) if traced else
                            self.backend.execute(step.action, step.parameters or {}))
                return self.backend.act(step.action)

            with phase("dispatch", self._diagnostic_trace):
                outcome = self._trace.dispatch(
                    dispatch, step.action, parameters=step.parameters,
                    plan_id=plan.id, step_index=index, pending=self.memory.pending,
                    checkpointed=self.checkpoint is not None, attempt_id=self.memory.attempt["id"])
        except ResearchLogError:
            # A failed recorder is not an ambiguous backend return and must not
            # be swallowed by the normal dispatch-error handling.
            raise
        except Exception as error:
            self.memory.pending["dispatch"] = "ambiguous"
            self.memory.event("dispatch_error", error_type=error_code(error), tick=fresh.tick)
            return self._record(snapshot, step.action, "Ambiguous dispatch; verification required", fresh)
        self.memory.pending["dispatch"] = "returned"
        self._save()
        self._trace.observation_phase = "after_dispatch"
        after = self._observe("post_dispatch_observe")
        if self._execution_barrier(after):
            return self._record(snapshot, step.action,
                                str(outcome) + "; pending retained for reconciliation", after)
        with phase("verification", self._diagnostic_trace):
            verified = self._trace.verify(step, after, plan_id=plan.id, index=index,
                                          pending=self.memory.pending, phase="post_dispatch",
                                          attempt_id=self.memory.attempt["id"])
        if verified:
            self._finish_attempt(after)
            self.memory.release(plan.id)
            self.memory.pending = None
            self._trace.clear_pending()
            self.memory.step_index += 1
            self.memory.stalled_decisions = 0
            self.memory.event("step_verified", plan=plan.id, action=step.action, tick=after.tick)
            if self.memory.step_index == len(plan.steps):
                self._clear_plan()
            self._refresh_goals(after)
        return self._record(snapshot, step.action, str(outcome), after, verified)
