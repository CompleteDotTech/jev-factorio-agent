"""Compose input routes with output buffers and optional acknowledged crafting."""
from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field

from .input_routes import MAX_BELTS, current, permits, sources
from .production_sites import sources as production_sites, summary as site_summary
from .planning.input_routes import InputRoutePlanner
from .skills import Plan, Step


class InputRouteMixin:
    planner_type = InputRoutePlanner

    def __init__(self, backend, jev=None, **options) -> None:
        self._input_fault = False
        self._input_evidence = {}
        super().__init__(backend, jev, **options)
        native = getattr(backend, "_factory", None)
        if native is not None:
            from .backends import has_adapter
            from .backends.input_routes import InputRouteFactory

            if not has_adapter(native, InputRouteFactory):
                backend._factory = InputRouteFactory(native)
        elif getattr(backend, "input_routes_supported", False) is not True:
            raise ValueError("Backend does not support input-route evidence")

    def _observe(self, stage="observe"):
        snapshot = super()._observe(stage)
        try:
            rows = sources(snapshot)
            production_sites(snapshot)  # Fail closed for stale or replaced joint-site evidence.
            for source, expected in self.memory.input_commitments.items():
                row = rows.get(source)
                if (not row or row["state"] == "proposed" or row["layout"] != expected["layout"]
                        or row["source_unit"] != expected["source_unit"]
                        or any(row["parts"].get(part) != receipt for part, receipt in expected["parts"].items())):
                    raise ValueError("Input-route commitment disappeared or regressed")
            for source, row in rows.items():
                if not current(row, snapshot):
                    raise ValueError("Input-route identity or flow requires reconciliation")
                if row["state"] != "proposed":
                    self.memory.input_commitments[source] = {
                        "layout": row["layout"], "source_unit": row["source_unit"],
                        "parts": deepcopy(row["parts"]),
                    }
        except (ValueError, KeyError, TypeError, AttributeError):
            self._input_fault = True
            self.memory.status, self.memory.reason = "uncertain", "Input-route evidence invalid; preserve pending work"
        self._input_evidence = deepcopy(snapshot.factory.get("input_routes", {}))
        self._save()  # Persist newly observed commitments before another mutation.
        return snapshot

    def _execution_barrier(self, snapshot) -> bool:
        return self._input_fault or super()._execution_barrier(snapshot)

    def _step_allowed(self, step, snapshot) -> bool:
        return (not self._execution_barrier(snapshot)
                and permits(step.action, step.parameters or {}, snapshot)
                and super()._step_allowed(step, snapshot))

    def _record_extras(self) -> dict:
        return {**super()._record_extras(), "furnace_input_belts": True,
                "input_route_evidence": deepcopy(self._input_evidence)}

    def _model_facts(self, snapshot) -> dict:
        facts = super()._model_facts(snapshot)
        # Keep full authoritative observations/checkpoints/logs. Only the model
        # view omits dozens of repeated belt inventories and layout coordinates.
        rows = sources(snapshot)
        summary = {}
        for source, row in rows.items():
            for part, entry in row["parts"].items():
                if part.startswith("belt:"):
                    facts["factory"]["entities"].pop(entry["role"], None)
            todo = [step for step in row["steps"] if step["part"] not in row["parts"]]
            summary[source] = {key: deepcopy(row[key]) for key in (
                "layout", "source_unit", "ore", "item", "state", "topology", "flow", "reserve_belts")}
            summary[source].update(belt_count=len(row["steps"])-2, paid_components=len(row["parts"]),
                                   next_component=deepcopy(todo[0]) if todo else None)
        facts["factory"]["input_routes"] = {"protocol": 1, "sources": summary,
            "diagnostics": deepcopy(snapshot.factory.get("input_routes", {}).get("diagnostics", {}))}
        if "production_sites" in facts["factory"]:
            facts["factory"]["production_sites"] = {"protocol": 1, "sources": site_summary(snapshot)}
        return facts

    def _compile_candidates(self, snapshot):
        # OutputBufferMixin uses planner_type, preserving its boiler, output-arm,
        # background-lock, and research-prefetch priorities.
        plans, blocker = super()._compile_candidates(snapshot)
        if self.memory.active_goal == "bootstrap_mining":
            return plans, blocker
        planner = InputRoutePlanner(self.catalog, snapshot, self.memory.active_goal)
        boiler = snapshot.factory.get("entities", {}).get("utility:boiler", {})
        if not boiler or boiler.get("fuel", {}).get("coal", 0) >= 5:
            for row in sources(snapshot).values():
                if len(row["steps"]) != len(row["parts"]) or not row["topology"]:
                    continue
                for name in ("inserter", "drill"):
                    part = row["parts"][name]
                    fuel = snapshot.factory["entities"][part["role"]].get("fuel", {}).get("coal", 0)
                    if fuel < 2:
                        plan = planner._acquire("coal", 5-fuel, ()) or planner._transfer(part["role"], "coal", 5-fuel)
                        if self._step_allowed(plan.steps[0], snapshot):
                            return [plan], ""
        selected = [plan for plan in plans if self._step_allowed(plan.steps[0], snapshot)]
        if selected:
            return selected, blocker
        job = getattr(self, "_job", lambda: None)()
        if job is not None:
            return [Plan("input:background-wait", self.memory.active_goal,
                         "No independent route-safe work; observe acknowledged crafting",
                         (Step("factory_wait", "crafting_idle", timeout_ticks=1800),))], ""
        return [], blocker or "No input-route-safe production action"


def input_loop_type(base):
    """Named checkpoint extension; legacy readers reject it rather than dropping ownership."""
    @dataclass
    class InputMemory(base.memory_type):
        input_routes_schema: int = 1
        input_commitments: dict = field(default_factory=dict)

        @classmethod
        def load(cls, path, session_id, target):
            memory = super().load(path, session_id, target)
            if (type(memory.input_routes_schema) is not int or memory.input_routes_schema != 1
                    or not isinstance(memory.input_commitments, dict) or len(memory.input_commitments) > 2):
                raise ValueError("Invalid input-route checkpoint extension")
            from .input_routes import ORES
            for source, entry in memory.input_commitments.items():
                if (source not in ORES or not isinstance(entry, dict)
                        or set(entry) != {"layout", "source_unit", "parts"}
                        or not isinstance(entry["layout"], str) or not 0 < len(entry["layout"]) <= 128
                        or type(entry["source_unit"]) is not int or entry["source_unit"] <= 0
                        or not isinstance(entry["parts"], dict) or len(entry["parts"]) > 66):
                    raise ValueError("Invalid input-route ownership checkpoint")
                units, roles, receipts = {entry["source_unit"]}, set(), set()
                for part, paid in entry["parts"].items():
                    if (part not in {"drill", "inserter", *[f"belt:{i}" for i in range(1, MAX_BELTS+1)]}
                            or not isinstance(paid, dict)
                            or set(paid) != {"role", "receipt", "unit_number", "paid"}
                            or any(not isinstance(paid[k], str) or not 0 < len(paid[k]) <= 128
                                   for k in ("role", "receipt"))
                            or type(paid["unit_number"]) is not int or paid["unit_number"] <= 0
                            or type(paid["paid"]) is not int or paid["paid"] != 1
                            or paid["unit_number"] in units or paid["role"] in roles or paid["receipt"] in receipts):
                        raise ValueError("Invalid input-route paid ownership checkpoint")
                    units.add(paid["unit_number"])
                    roles.add(paid["role"])
                    receipts.add(paid["receipt"])
            return memory

        def save(self, path):
            super().save(path)
            if path is not None and os.name == "posix":
                descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)

    return type("InputRouteLoop", (InputRouteMixin, base), {"memory_type": InputMemory, "__module__": __name__})
