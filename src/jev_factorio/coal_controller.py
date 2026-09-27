"""Explicit coal/solid controller composition, with durable whole-bundle ownership.

This is source-only experimental integration. No production CLI/supervisor flag
is exposed; pinned native qualification and an authorized immutable handoff remain
separate gates. This module never resets a campaign or changes its cutoff.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path

from . import coal_supply as coal, solid_routes as solid
from .backends.coal_supply import CoalSupplyFactory
from .planning.coal_supply import MARKER, candidates
from .planning.demand import SupplyLedger
from .research_log import RunConfiguration, ResearchLogError
from .skills import Plan
from .solid_controller import UNBOUND_FAULT as SOLID_UNBOUND
from .telemetry import phase

CHECKPOINT_FIELDS = {"coal_supply_schema", "coal_targets", "coal_epoch", "coal_commitments"}
UNBOUND_FAULT = "Coal-source epoch unbound; native reconciliation required"


class CoalSupplyMixin:
    def __init__(self, backend, jev=None, *, coal_targets, **options) -> None:
        self._coal_targets = coal.validate_targets(coal_targets)
        self._coal_fault = False
        self._coal_evidence = {}
        coal.validate_transport_intents(self._coal_targets, options.get("solid_intents"))
        sink = options.get("research_log")
        if sink is not None and (not isinstance(getattr(sink, "configuration", None), RunConfiguration)
                                 or sink.configuration.coal_supply is not True):
            raise ValueError("Research manifest must explicitly bind the coal treatment")
        if options.get("resume_controller"):
            raw = Path(options["checkpoint"]).read_bytes()
            data = json.loads(raw)
            if (not isinstance(data, dict) or not CHECKPOINT_FIELDS <= data.keys()
                    or data["coal_targets"] != self._coal_targets or not data["coal_epoch"]):
                raise ValueError("Coal treatment cannot adopt or migrate an unbound checkpoint")
            # The inner solid initializer does the full composed memory validation
            # plus sticky pre/post-observation byte checks before any mutation.
        super().__init__(backend, jev, **options)
        native = getattr(backend, "_factory", None)
        if native is not None:
            current = native
            while current is not None and not isinstance(current, CoalSupplyFactory):
                current = getattr(current, "native", None)
            if current is None:
                backend._factory = CoalSupplyFactory(native, self._coal_targets)
            elif current.targets != self._coal_targets:
                raise ValueError("Existing native coal treatment differs")
        elif getattr(backend, "coal_supply_supported", False) is not True:
            raise ValueError("Backend does not support owned coal source observations")

    def _observe_snapshot(self):
        snapshot = super()._observe_snapshot()
        if not self.memory.coal_targets:
            self.memory.coal_targets = list(self._coal_targets)
        if not self.memory.coal_epoch:
            try:
                coal.sources(snapshot)
                self.memory.coal_epoch = {k: snapshot.factory["coal_supply"][k]
                                         for k in ("actor_index", "surface_index", "force_index")}
            except (ValueError, KeyError, TypeError, AttributeError):
                self._coal_fault = True
                self.memory.status, self.memory.reason = "uncertain", UNBOUND_FAULT
        return snapshot

    def _observe_solid(self, stage="observe"):
        # The solid layer owns the outer first-resume transaction. Coal-specific
        # validation and saves must finish inside that same publication barrier.
        snapshot = super()._observe_solid(stage)
        try:
            rows = coal.sources(snapshot)
            data = snapshot.factory["coal_supply"]
            epoch = {k: data[k] for k in ("actor_index", "surface_index", "force_index")}
            if (self.memory.coal_targets != self._coal_targets or data["targets"] != self._coal_targets
                    or self.memory.coal_epoch != epoch or any(not coal.current(row, snapshot) for row in rows.values())):
                raise ValueError("Coal source binding or ownership changed")
            from .construction_journal import require_owner
            for target, row in rows.items():
                require_owner(self.memory, action=coal.COMMAND, binding={"target": target},
                              layout=row["layout"], journal=row["pending"])
            plan = Plan.from_dict(self.memory.active_plan) if self.memory.active_plan else None
            step = plan.steps[self.memory.step_index] if plan else None
            tracked = self.memory.coal_commitments
            if tracked and (not data["committed"] or set(rows) != set(tracked)):
                raise ValueError("Coal commitment disappeared")
            if data["committed"] and not tracked:
                if not self.memory.pending or step is None or step.action != coal.COMMAND:
                    raise ValueError("Untracked native coal bundle")
                evidence = (plan.materials or {}).get(MARKER, {}).get("bundle")
                if not isinstance(evidence, dict) or set(evidence) != set(rows):
                    raise ValueError("Missing prepared whole-bundle commitment")
                for target, saved in evidence.items():
                    coal.validate_commitment(saved, target)
                    if not coal.reconciles(saved, rows[target]):
                        raise ValueError("Native coal bundle differs from prepared geometry")
                tracked = deepcopy(evidence)
            for target, saved in tracked.items():
                row = rows[target]
                if not coal.reconciles(saved, row):
                    raise ValueError("Coal source identity or payment regressed")
                new_parts = set(row["parts"]) - set(saved["parts"])
                if new_parts:
                    p = step.parameters if step else {}
                    if (not self.memory.pending or step is None or step.action != coal.COMMAND
                            or p["target"] != target or p["layout"] != row["layout"]
                            or new_parts != {p["part"]} or row["parts"][p["part"]]["receipt"] != p["receipt"]):
                        raise ValueError("Untracked paid coal part")
            if data["committed"]:
                self.memory.coal_commitments = {target: coal.commitment(row) for target, row in rows.items()}
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            self._coal_fault = True
            self.memory.status = "uncertain"
            self.memory.reason = ("Coal-source evidence invalid; preserve pending work and ownership"
                                  if self.memory.coal_epoch else UNBOUND_FAULT)
        self._coal_evidence = deepcopy(snapshot.factory.get("coal_supply", {}))
        self._save()
        return snapshot

    def _execution_barrier(self, snapshot):
        return self._coal_fault or super()._execution_barrier(snapshot)

    def _step_allowed(self, step, snapshot):
        if self._execution_barrier(snapshot) or not coal.permits(step.action, step.parameters or {}, snapshot):
            return False
        try:
            rows = coal.sources(snapshot)
            network = step.action == coal.COMMAND or (step.action == solid.COMMAND and coal.is_network_route(step.parameters, snapshot))
            bill = Counter(coal.remaining_kit(rows, snapshot)) if network or self.memory.coal_commitments else Counter()
            own = (step.parameters or {}).get("route") if step.action == solid.COMMAND else None
            if not network:
                bill.update(solid.remaining(solid.routes(snapshot)[own]) if own else step.costs or {})
            reserved = Counter()
            # Coal corridors are already in the whole-network bill; do not count
            # them twice. Other route, background and active-plan locks survive.
            for key, saved in self.memory.solid_commitments.items():
                if key != own and saved["source"]["role"] not in {coal.role(t, "chest") for t in rows}:
                    reserved.update(solid.remaining(saved))
            active = (self.memory.active_plan or {}).get("id")
            for owner, held in self.memory.reservations.items():
                if owner != active:
                    reserved.update(held)
            ledger = SupplyLedger.capture(snapshot, self.catalog, reserved=dict(reserved),
                                           job=getattr(self, "_job", lambda: None)())
            if any(ledger.carried.get(item, 0) < n for item, n in bill.items()):
                return False
        except (ValueError, KeyError, TypeError, AttributeError):
            return False
        return super()._step_allowed(step, snapshot)

    def _solid_reservations(self):
        reserved = Counter(super()._solid_reservations())
        # Solid already accounts for each committed corridor. Source components
        # and not-yet-committed receiving corridors are part of the same durable
        # coal bundle, even before a chest has produced an observable route.
        reserved.update(coal.reserved_components(self.memory.coal_commitments, self.memory.solid_commitments))
        return dict(reserved)

    def _compile_candidates(self, snapshot):
        plans, blocker = super()._compile_candidates(snapshot)
        if self.memory.active_goal == "bootstrap_mining" or self._execution_barrier(snapshot):
            return plans, blocker
        # Replace only this network's ordinary solid proposals with the balanced
        # bundle proposals. The original useful production frontier is not rerun.
        plans = [p for p in plans if not (p.steps[0].action == solid.COMMAND
                                         and coal.is_network_route(p.steps[0].parameters, snapshot))]
        extra = candidates(snapshot, self.memory.active_goal, failures=self.memory.failures)
        plans.extend(p for p in extra if p.id not in {p.id for p in plans} and self._step_allowed(p.steps[0], snapshot))
        return plans, blocker if not plans else ""

    def _verify_pending(self, snapshot):
        pending = self.memory.pending
        if pending and not self._execution_barrier(snapshot):
            plan = Plan.from_dict(self.memory.active_plan)
            step = plan.steps[self.memory.step_index]
            if step.action == coal.COMMAND:
                row = coal.sources(snapshot).get(step.parameters["target"])
                proof = {"part": step.parameters["part"], "receipt": step.parameters["receipt"], "phase": "prepared"}
                if (pending.get("dispatch") in {"prepared", "ambiguous"} and pending.get("polls") == 0
                        and row and row["pending"] == proof and self._step_allowed(step, snapshot)
                        and self._investment_step_allowed(plan, step, snapshot)):
                    pending["polls"] = 1
                    self.memory.event("coal_prepared_recovery", plan=plan.id, tick=snapshot.tick)
                    self._save()
                    try:
                        with phase("dispatch", self._diagnostic_trace):
                            self._trace.dispatch(lambda: (self.backend.execute_traced(step.action, step.parameters, self._diagnostic_trace)
                                if getattr(self.backend, "execute_traced", None) else self.backend.execute(step.action, step.parameters)),
                                step.action, parameters=step.parameters, plan_id=plan.id,
                                step_index=self.memory.step_index, pending=pending,
                                checkpointed=True, attempt_id=self.memory.attempt["id"])
                    except ResearchLogError:
                        raise
                    except Exception:
                        pending["dispatch"] = "ambiguous"
                        self._save()
                        return self._record(snapshot, "observe", "Exact coal replay remains ambiguous; preserve pending receipt")
                    pending["dispatch"] = "returned"
                    self._save()
                    self._trace.observation_phase = "post_recovery_dispatch"
                    snapshot = self._observe("post_dispatch_observe")
        return super()._verify_pending(snapshot)

    def _record_extras(self):
        return {**super()._record_extras(), "coal_supply": True,
                "coal_supply_evidence": deepcopy(self._coal_evidence), "coal_supply_fault": self._coal_fault}

    def _model_facts(self, snapshot):
        facts = super()._model_facts(snapshot)
        summary = {}
        for target, row in coal.sources(snapshot).items():
            for paid in row["parts"].values():
                facts["factory"].get("entities", {}).pop(paid["role"], None)
            summary[target] = {"state": row["state"], "paid_source_parts": len(row["parts"]),
                               "remaining_coal": row["remaining"], "reason": row["reason"],
                               "delivered_lower": row["flow"].get("delivered_lower", 0),
                               "manual_inserted": row["flow"].get("manual_inserted", 0)}
        facts["factory"]["coal_supply"] = {"sources": summary, "network_flow_observed": coal.flow_complete(snapshot)}
        return facts


def coal_loop_type(base):
    if not getattr(base, "_solid_routes_enabled", False):
        raise ValueError("Coal composition requires the existing solid-route controller")

    @dataclass
    class CoalMemory(base.memory_type):
        coal_supply_schema: int = 1
        coal_targets: list = field(default_factory=list)
        coal_epoch: dict = field(default_factory=dict)
        coal_commitments: dict = field(default_factory=dict)

        @classmethod
        def _from_data(cls, data, session_id, target):
            if not isinstance(data, dict) or not CHECKPOINT_FIELDS <= data.keys():
                raise ValueError("Incomplete coal checkpoint extension")
            memory = super()._from_data(data, session_id, target)
            if not solid.integer(memory.coal_supply_schema, 1, 1):
                raise ValueError("Unsupported coal checkpoint schema")
            coal.validate_targets(memory.coal_targets)
            coal.validate_transport_intents(memory.coal_targets, memory.solid_intents)
            if not isinstance(memory.coal_epoch, dict) or not isinstance(memory.coal_commitments, dict):
                raise ValueError("Invalid coal checkpoint binding")
            if not memory.coal_epoch:
                if (memory.coal_commitments or memory.status != "uncertain"
                        or memory.reason not in {UNBOUND_FAULT, SOLID_UNBOUND, "Solid-route evidence invalid; preserve pending state and ownership"}):
                    raise ValueError("Invalid unbound coal checkpoint")
            elif memory.coal_epoch != memory.solid_epoch:
                raise ValueError("Coal checkpoint epoch differs from its transport")
            commitments = memory.coal_commitments
            if commitments and set(commitments) != set(memory.coal_targets):
                raise ValueError("Coal checkpoint lost a consumer")
            units, receipts, areas, layouts = set(), set(), [], set()
            for consumer, saved in commitments.items():
                coal.validate_commitment(saved, consumer)
                ids = {saved["target"]["unit_number"], *[v["unit_number"] for v in saved["parts"].values()]}
                paid = {v["receipt"] for v in saved["parts"].values()}
                area = coal._bounds(saved["mining_area"])
                if units & ids or receipts & paid or any(coal._overlap(area, old) for old in areas):
                    raise ValueError("Coal checkpoint shares ownership or mining areas")
                units.update(ids); receipts.update(paid); areas.append(area); layouts.add(saved["layout"])
            if len(layouts) > 1:
                raise ValueError("Coal checkpoint mixes bundle generations")
            return memory

    return type("CoalSupplyLoop", (CoalSupplyMixin, base), {"memory_type": CoalMemory, "__module__": __name__})
