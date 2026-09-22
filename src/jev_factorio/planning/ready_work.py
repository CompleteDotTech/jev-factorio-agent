"""Opt-in, bounded ready-work choices over the existing native action contract.

This is not a concurrent dispatcher or a belt planner. Forecasts influence only
batching/priorities; the unchanged native preconditions and postconditions own
execution. The serial planner remains the fallback for unsupported production.
"""
from __future__ import annotations

import math
from dataclasses import replace

from ..skills import Plan
from ..state import GameSnapshot
from .catalog import Catalog
from .demand import SupplyLedger, horizon_demands
from .service_visits import service_visit
from .scheduling import scheduled_research_wait, ready_research_work
from .factory import FactoryPlanner, RAW_ITEMS, compile_factory
from .economics import EconomicProduction


class ReadyWorkPlanner(EconomicProduction, FactoryPlanner):
    def __init__(self, catalog: Catalog, snapshot: GameSnapshot, goal: str,
                 collection_batch: int = 10, max_candidates: int = 8) -> None:
        super().__init__(catalog, snapshot, goal)
        if type(collection_batch) is not int or not 1 <= collection_batch <= 50:
            raise ValueError("Collection batch must be an integer in [1, 50]")
        if type(max_candidates) is not int or not 1 <= max_candidates <= 16:
            raise ValueError("Candidate budget must be an integer in [1, 16]")
        self.collection_batch = collection_batch
        self.max_candidates = max_candidates
        self.focus: tuple[str, int] | None = None
        self.targets: dict[str, int] = {}
        self.raw_targets: dict[str, int] = {}
        self.demands: dict[str, int] = {}
        self.ledger = SupplyLedger.capture(snapshot, catalog)
        self.speculative = False
        self.allow_service_visits = True

    def _plan(self, *args, **kwargs) -> Plan:
        plan = super()._plan(*args, **kwargs)
        if self.focus is None:
            return plan
        return replace(plan, materials={**(plan.materials or {}), "local_objective": {
            "item": self.focus[0], "inventory_target": self.focus[1],
            "ultimate_goal": self.goal,
        }})

    def plan(self):
        return self._capacity_work(ready_research_work(self, super().plan()))

    def _wait(self, effect, item="", threshold=0, role="", timeout=36000, identity=None):
        plan = super()._wait(effect, item, threshold, role, timeout, identity)
        return scheduled_research_wait(self, plan)

    def _set_focus(self, item: str, amount: int) -> None:
        self.focus = (item, math.ceil(amount))
        self.demands = horizon_demands(self.snapshot, self.catalog, self.goal, item, amount)
        try:
            bill = self.catalog.material_demands(
                self.demands, self.ledger.forecast_stock(), self.researched)
        except (KeyError, ValueError):
            return  # Unsupported lookahead never authorizes a speculative action.
        for name, batches in bill.batches.items():
            for ingredient in self.catalog.recipes[name]["ingredients"]:
                if ingredient["type"] != "item":
                    continue
                material = ingredient["name"]
                self.targets[material] = self.targets.get(material, 0) + math.ceil(
                    ingredient["amount"] * batches
                )
        for material, quantity in bill.shortages.items():
            if material in RAW_ITEMS and material != "wood":
                target = self.snapshot.inventory.get(material, 0) + math.ceil(quantity)
                self.raw_targets[material] = target
        # Do not gather raw inputs already paid into machines just because an
        # intermediate recipe appears in the material bill.
        for material in RAW_ITEMS:
            self.targets.pop(material, None)
        self.targets.update(self.raw_targets)

    def _batch_collection(self, plan: Plan, amount: int) -> Plan:
        step = plan.steps[0]
        if step.action != "factory_extract":
            return plan
        parameters = step.parameters
        role, item = parameters["role"], parameters["item"]
        machine = self.entities[role]
        # Batch dedicated, actively producing deterministic solid-item machines.
        # A chest, stopped machine, mixed fluid recipe, or missing telemetry is
        # not evidence that a larger output will arrive.
        recipe_name = machine.get("recipe") or role.removeprefix("recipe:")
        recipe = self.catalog.recipes.get(recipe_name, {})
        if (not role.startswith(("recipe:", "capacity:")) or not recipe
                or machine.get("crafting") is not True
                or any(entry["type"] != "item" for entry in recipe.get("ingredients", []))):
            return plan
        prototype = self.catalog.machines.get(machine["name"], {})
        if prototype.get("burner") and machine.get("fuel", {}).get("coal", 0) < 5:
            return plan
        if prototype.get("electric") and machine.get("energy", 0) <= 0:
            return plan
        products = [entry for entry in recipe.get("products", [])
                    if entry["name"] == item and entry.get("probability", 1) == 1]
        if len(products) != 1 or not recipe.get("ingredients"):
            return plan
        output = products[0].get("amount", 0)
        if output <= 0:
            return plan
        available = machine.get("output", {}).get(item, 0)
        buffered_batches = min(
            math.floor(machine.get("input", {}).get(entry["name"], 0) / entry["amount"])
            for entry in recipe["ingredients"] if entry["amount"] > 0
        )
        potential = available + output * (buffered_batches + 1)
        target = min(self.collection_batch, amount - self.snapshot.inventory.get(item, 0),
                     potential)
        if target <= available:
            return plan
        return self._wait(
            "machine_output", item, target, role,
            timeout=max(3600, math.ceil(recipe["energy"] * self.collection_batch * 120)),
            identity=f"batch:{role}:{item}:target:{target}",
        )

    def _need(self, item, amount, path=()):
        if self.focus is None and self.snapshot.inventory.get(item, 0) < amount:
            self._set_focus(item, amount)
        if item in self.raw_targets and self.snapshot.inventory.get(item, 0) < amount:
            amount = max(amount, self.raw_targets[item])
        # Do not create a dedicated trip for the one-unit edge of a speculative
        # horizon. The immediate prerequisite path is never suppressed.
        if (self.speculative and item in RAW_ITEMS - {"wood"}
                and 0 < amount - self.snapshot.inventory.get(item, 0) < self.collection_batch):
            return None
        plan = super()._need(item, amount, path)
        # Only the item whose need produced this extraction may set its batch
        # target. An ancestor recipe can require many outputs but few plates.
        if plan and (plan.steps[0].parameters or {}).get("item") == item:
            batched = self._batch_collection(plan, amount)
            if batched.steps[0].action == "factory_wait":
                missing = math.ceil(amount - self.snapshot.inventory.get(item, 0))
                for role, machine in sorted(self.entities.items()):
                    available = machine.get("output", {}).get(item, 0)
                    if available and role != plan.steps[0].parameters["role"]:
                        alternative = self._batch_collection(
                            self._transfer(role, item, min(missing, available), extracting=True),
                            amount,
                        )
                        if alternative.steps[0].action == "factory_extract":
                            return alternative
            return batched
        return plan

    def candidates(self) -> list[Plan]:
        primary = self.plan()
        if primary is None:
            return []
        # Binding, in-flight handcrafting, and infrastructure prerequisites stay
        # serial. Nothing here releases a pending mutation or spends its inputs.
        if (primary.steps[0].action not in {
                "factory_gather", "factory_insert", "factory_extract", "factory_wait"
            } or not self.focus or self.factory.get("crafting_queue", 0)):
            return [primary]
        candidates = [primary]
        # Evaluate a bounded frontier, not every item in a rocket-sized tree.
        for item, amount in list(sorted(self.targets.items()))[:32]:
            if self.snapshot.inventory.get(item, 0) >= amount:
                continue
            worker = type(self)(self.catalog, self.snapshot, self.goal,
                                      self.collection_batch, self.max_candidates)
            worker.focus, worker.raw_targets = self.focus, dict(self.raw_targets)
            worker.materials = self.materials or {}
            worker.ledger, worker.demands = self.ledger, dict(self.demands)
            worker.speculative = True
            worker.allow_service_visits = self.allow_service_visits
            try:
                plan = worker._need(item, amount)
            except (KeyError, ValueError):
                continue
            if plan and plan.steps[0].action in {
                "factory_gather", "factory_insert", "factory_extract", "factory_craft"
            }:
                candidates.append(plan)
        unique = {}
        for plan in candidates:
            step = plan.steps[0]
            if step.allowed(self.snapshot) and not step.satisfied(self.snapshot):
                unique.setdefault(plan.id, plan)
        ready = [plan for plan in unique.values() if plan.steps[0].action != "factory_wait"]
        # A useful primary retains deterministic priority. A passive wait never
        # outranks executable independent work; JEV sees the remaining options.
        selected = ready or list(unique.values()) or [primary]
        item, amount = self.focus
        prefix = f"Next production batch: {amount} {item}. "
        return [service_visit(self, replace(plan, description=prefix + plan.description))
                for plan in selected[:self.max_candidates]]


def compile_ready_factory(goal: str, snapshot: GameSnapshot,
                          catalog: Catalog) -> tuple[list[Plan], str]:
    if goal == "bootstrap_mining":
        return compile_factory(goal, snapshot, catalog)
    try:
        plans = ReadyWorkPlanner(catalog, snapshot, goal).candidates()
        return plans, "" if plans else "No remaining native production action"
    except (ValueError, KeyError) as error:
        return [], str(error)
