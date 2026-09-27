"""Explicit experimental composition for paid solid routes.

Not enabled by CLI or supervisor. Native qualification and an authorized immutable
runtime handoff are required before making this a production treatment.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path

from . import solid_routes as routes
from .backends.solid_routes import SolidRouteFactory, validate_intents
from .planning.solid_routes import candidates
from .planning.demand import SupplyLedger
from .skills import Plan
from .research_log import ResearchLogError, RunConfiguration
from .telemetry import phase

CHECKPOINT_FIELDS = {"solid_routes_schema", "solid_intents", "solid_epoch", "solid_commitments"}


class SolidRouteMixin:
    _solid_routes_enabled = True

    def __init__(self, backend, jev=None, *, solid_intents, **options) -> None:
        self._solid_intents = validate_intents(solid_intents)
        self._solid_fault = False
        self._solid_evidence = {}
        if options.get("factory_scheduling") != "ready-work":
            raise ValueError("Solid routes require explicit ready-work scheduling")
        if not options.get("checkpoint"):
            raise ValueError("Solid routes require a durable controller checkpoint")
        sink = options.get("research_log")
        if sink is not None:
            configuration = getattr(sink, "configuration", None)
            if (not isinstance(configuration, RunConfiguration) or configuration.solid_routes is not True
                    or configuration.factory_scheduling != "ready-work"):
                raise ValueError("Research manifest must explicitly declare the solid-route treatment")
        # Validate immutable treatment before attaching any native extension.
        if options.get("resume_controller"):
            saved = json.loads(Path(options["checkpoint"]).read_text())
            if (not isinstance(saved, dict) or not CHECKPOINT_FIELDS <= saved.keys()
                    or saved["solid_intents"] != self._solid_intents):
                raise ValueError("Solid treatment cannot silently replace or migrate a checkpoint")
        super().__init__(backend, jev, **options)
        native = getattr(backend, "_factory", None)
        if native is not None:
            current = native
            while current is not None and not isinstance(current, SolidRouteFactory):
                current = getattr(current, "native", None)
            if current is None:
                backend._factory = SolidRouteFactory(native, self._solid_intents)
            elif current.intents != self._solid_intents:
                raise ValueError("Existing native solid treatment differs")
        elif getattr(backend, "solid_routes_supported", False) is not True:
            raise ValueError("Backend does not support owned solid-route observations")

    def _observe_snapshot(self):
        snapshot = super()._observe_snapshot()
        # Inner composed observers may save their own new ownership before the
        # outer observation completes. Bind this extension first, so a crash
        # there leaves a reloadable checkpoint rather than empty treatment fields.
        if not self.memory.solid_intents:
            self.memory.solid_intents = deepcopy(self._solid_intents)
        if not self.memory.solid_epoch:
            try:
                routes.routes(snapshot)
                self.memory.solid_epoch = {key: snapshot.factory["solid_routes"][key]
                                          for key in ("actor_index", "surface_index", "force_index")}
            except (ValueError, KeyError, TypeError, AttributeError):
                pass  # The outer observer records an uncertain state, never acts.
        return snapshot

    def _observe(self, stage="observe"):
        snapshot = super()._observe(stage)
        try:
            rows = routes.routes(snapshot)
            epoch = {key: snapshot.factory["solid_routes"][key]
                     for key in ("actor_index", "surface_index", "force_index")}
            if self.memory.solid_epoch and self.memory.solid_epoch != epoch:
                raise ValueError("Solid route actor/surface/force changed")
            if self.memory.solid_intents and self.memory.solid_intents != self._solid_intents:
                raise ValueError("Solid route intent binding changed")
            intents = {(i["source"], i["target"], i["item"], i["destination"]) for i in self._solid_intents}
            for key, saved in self.memory.solid_commitments.items():
                if key not in rows or not routes.reconciles(saved, rows[key]):
                    raise ValueError("Paid solid commitment disappeared or regressed")
            for key, row in rows.items():
                if not routes.current(row, snapshot):
                    raise ValueError("Solid route requires native reconciliation")
                if (row["source"]["role"], row["target"]["role"], row["item"], row["target"]["inventory"]) not in intents:
                    raise ValueError("Unrequested solid route")
                if row["state"] != "proposed":
                    if key not in self.memory.solid_commitments:
                        plan = Plan.from_dict(self.memory.active_plan) if self.memory.active_plan else None
                        step = plan.steps[self.memory.step_index] if plan else None
                        if (not self.memory.pending or step is None or step.action != routes.COMMAND
                                or (step.parameters or {}).get("route") != key
                                or step.parameters.get("layout") != row["layout"]):
                            raise ValueError("Untracked native solid commitment requires reconciliation")
                    self.memory.solid_commitments[key] = routes.commitment(row)
            self.memory.solid_epoch = epoch
            self.memory.solid_intents = deepcopy(self._solid_intents)
        except (ValueError, TypeError, KeyError, AttributeError):
            self._solid_fault = True
            self.memory.status, self.memory.reason = "uncertain", "Solid-route evidence invalid; preserve pending state and ownership"
        self._solid_evidence = deepcopy(snapshot.factory.get("solid_routes", {}))
        self._save()  # Exact paid prefix is durable before another actor mutation.
        return snapshot

    def _execution_barrier(self, snapshot):
        return self._solid_fault or super()._execution_barrier(snapshot)

    def _step_allowed(self, step, snapshot):
        if self._execution_barrier(snapshot) or not routes.permits(step.action, step.parameters or {}, snapshot):
            return False
        try:
            own = (step.parameters or {}).get("route") if step.action == routes.COMMAND else None
            reserved = Counter()
            # The durable commitment is the kit lock. Reuse SupplyLedger's
            # spendable accounting; clearing a one-component Plan cannot release it.
            for key, saved in self.memory.solid_commitments.items():
                if key != own:
                    reserved.update(routes.remaining(saved))
            active = (self.memory.active_plan or {}).get("id")
            for owner, held in self.memory.reservations.items():
                if owner != active:
                    reserved.update(held)
            job = getattr(self, "_job", lambda: None)()
            ledger = SupplyLedger.capture(snapshot, self.catalog, reserved=dict(reserved), job=job)
            needed = (routes.remaining(routes.routes(snapshot)[own]) if own else step.costs or {})
            if any(ledger.carried.get(item, 0) < count for item, count in needed.items()):
                return False
        except (ValueError, KeyError, TypeError, AttributeError):
            return False
        return super()._step_allowed(step, snapshot)

    def _compile_candidates(self, snapshot):
        plans, blocker = super()._compile_candidates(snapshot)
        if self.memory.active_goal == "bootstrap_mining" or self._execution_barrier(snapshot):
            return plans, blocker
        # Compile the existing production frontier exactly once. Infrastructure
        # is appended, never an exclusive override of science or maintenance.
        extras = candidates(snapshot, self.memory.active_goal)
        merged = plans + [p for p in extras if p.id not in {p.id for p in plans}]
        allowed = [p for p in merged if self._step_allowed(p.steps[0], snapshot)]
        return allowed, blocker if not allowed else ""

    def _verify_pending(self, snapshot):
        pending = self.memory.pending
        if pending and not self._execution_barrier(snapshot):
            plan = Plan.from_dict(self.memory.active_plan)
            step = plan.steps[self.memory.step_index]
            row = routes.routes(snapshot).get((step.parameters or {}).get("route"))
            proof = {"part": (step.parameters or {}).get("part"),
                     "receipt": (step.parameters or {}).get("receipt"), "phase": "prepared"}
            # At most one exact idempotent replay, only after native write-ahead
            # evidence proves the ordinary actor placement has not started.
            if (step.action == routes.COMMAND and pending.get("dispatch") in {"prepared", "ambiguous"}
                    and pending.get("polls") == 0 and row and row["pending"] == proof
                    and self._step_allowed(step, snapshot)
                    and self._investment_step_allowed(plan, step, snapshot)):
                pending["polls"] = 1
                self.memory.event("solid_prepared_recovery", plan=plan.id, tick=snapshot.tick)
                self._save()
                try:
                    with phase("dispatch", self._diagnostic_trace):
                        self._trace.dispatch(
                            lambda: (self.backend.execute_traced(step.action, step.parameters, self._diagnostic_trace)
                                     if getattr(self.backend, "execute_traced", None)
                                     else self.backend.execute(step.action, step.parameters)),
                            step.action, parameters=step.parameters, plan_id=plan.id,
                            step_index=self.memory.step_index, pending=pending,
                            checkpointed=True, attempt_id=self.memory.attempt["id"])
                except ResearchLogError:
                    raise
                except Exception:
                    pending["dispatch"] = "ambiguous"
                    self._save()
                    return self._record(snapshot, "observe", "Exact solid replay remains ambiguous; preserve pending receipt")
                pending["dispatch"] = "returned"
                self._save()
                self._trace.observation_phase = "post_recovery_dispatch"
                after = self._observe("post_dispatch_observe")
                return super()._verify_pending(after)
        return super()._verify_pending(snapshot)

    def _record_extras(self):
        return {**super()._record_extras(), "solid_routes": True,
                "solid_route_evidence": deepcopy(self._solid_evidence), "solid_route_fault": self._solid_fault}

    def _model_facts(self, snapshot):
        facts = super()._model_facts(snapshot)
        summary = {}
        for key, row in routes.routes(snapshot).items():
            for part in row["parts"].values():
                facts["factory"].get("entities", {}).pop(part["role"], None)
            summary[key] = {"item": row["item"], "source": row["source"]["role"],
                            "target": row["target"]["role"], "state": row["state"],
                            "paid_components": len(row["parts"]), "total_components": len(row["steps"]),
                            "reason": row["reason"], "flow_verified": routes.flow_complete(key, row["layout"], snapshot)}
        facts["factory"]["solid_routes"] = {"protocol": 1, "routes": summary,
            "diagnostics": deepcopy(snapshot.factory["solid_routes"]["diagnostics"])}
        return facts


def solid_loop_type(base):
    @dataclass
    class SolidMemory(base.memory_type):
        solid_routes_schema: int = 1
        solid_intents: list = field(default_factory=list)
        solid_epoch: dict = field(default_factory=dict)
        solid_commitments: dict = field(default_factory=dict)

        @classmethod
        def load(cls, path, session_id, target):
            data = json.loads(Path(path).read_text())
            if not isinstance(data, dict) or not CHECKPOINT_FIELDS <= data.keys():
                raise ValueError("Incomplete or legacy solid-route checkpoint")
            memory = super().load(path, session_id, target)
            if not routes.integer(memory.solid_routes_schema, 1, 1):
                raise ValueError("Unsupported solid checkpoint version")
            validate_intents(memory.solid_intents)
            if (not isinstance(memory.solid_epoch, dict)
                    or set(memory.solid_epoch) != {"actor_index", "surface_index", "force_index"}
                    or any(not routes.integer(v, 1) for v in memory.solid_epoch.values())
                    or not isinstance(memory.solid_commitments, dict) or len(memory.solid_commitments) > routes.MAX_ROUTES):
                raise ValueError("Invalid solid checkpoint binding")
            units, receipts = set(), set()
            intents = {(i["source"], i["target"], i["item"], i["destination"]) for i in memory.solid_intents}
            for key, saved in memory.solid_commitments.items():
                routes.validate_commitment(saved, key)
                if (saved["source"]["role"], saved["target"]["role"], saved["item"], saved["target"]["inventory"]) not in intents:
                    raise ValueError("Checkpoint owns an unrequested route")
                ids = {saved["source"]["unit_number"], saved["target"]["unit_number"],
                       *[v["unit_number"] for v in saved["parts"].values()]}
                new_receipts = {v["receipt"] for v in saved["parts"].values()}
                if units & ids or receipts & new_receipts:
                    raise ValueError("Checkpoint double-owns a route component")
                units.update(ids); receipts.update(new_receipts)
            return memory

    return type("SolidRouteLoop", (SolidRouteMixin, base), {"memory_type": SolidMemory, "__module__": __name__})
