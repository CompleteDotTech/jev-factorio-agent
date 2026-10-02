"""Opt-in input routes layered on the existing output-buffer planner."""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace

from ..input_routes import COMMAND, flow_complete, remaining, sources
from ..production_sites import sources as production_sites, summary as site_summary
from ..output_buffers import flow_complete as output_flow_complete
from .output_buffers import OutputBufferPlanner


class InputRoutePlanner(OutputBufferPlanner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._acquiring_route = False

    def _machine(self, role, name, path, anchor="factory"):
        if role not in self.entities and name == "stone-furnace":
            row = production_sites(self.snapshot).get(role, {})
            if row.get("state") == "proposed":
                prerequisite = self._need(name, 1, path)
                if prerequisite:
                    return prerequisite
                plan = self._plan("factory_place", "machine",
                    parameters={"role": role, "name": name, "anchor": row["anchor"]},
                    costs={name: 1}, identity="joint:" + row["anchor"],
                    description=f"Place paid {name} at joint ore/route/output site for {role}")
                item_path = [entry.removeprefix('item:') for entry in path
                             if entry.startswith('item:')]
                if item_path[-1:] == [role.removeprefix('recipe:')]:
                    plan = replace(plan, materials={**(plan.materials or {}),
                        'placement_dependency': {
                            'observed_tick': self.snapshot.tick,
                            'planner_item_path': item_path,
                            'machine': name,
                            'source_role': role,
                            'site_anchor': row['anchor'],
                        }})
                return plan
        return super()._machine(role, name, path, anchor)

    def candidates(self):
        plans = super().candidates()
        sites = site_summary(self.snapshot)
        diagnostics = self.factory.get("input_routes", {}).get("diagnostics", {})
        return [replace(plan, materials={**(plan.materials or {}), "automation_sites": sites,
                                        "route_diagnostics": diagnostics}) for plan in plans]

    def _acquire(self, item, count, path):
        previous = self._acquiring_route
        economic = getattr(self, "_economic_acquiring", False)
        focus = self.focus
        parent = getattr(self, '_route_kit_parent', None)
        self._acquiring_route = self._economic_acquiring = True
        try:
            # Keep the output-buffer-aware ingredient path, not the raw serial
            # path that could try to extract from an automated furnace.
            # The kit is a separate bounded acquisition objective: it may need
            # a material from the outer consumer's path. Route expansion stays
            # disabled; cycles inside the kit and the shared expansion limit
            # still apply, and owned output-buffer access remains authoritative.
            if parent is not None and focus is not None:
                self.focus = (item, count)
            plan = super()._need(item, count, ())
            if plan is not None and parent is not None and focus is not None:
                parent_path = [entry.removeprefix('item:') for entry in path
                               if entry.startswith('item:')]
                if parent_path[-1:] != [parent['item']]:
                    parent_path.append(parent['item'])
                plan = replace(plan, materials={**(plan.materials or {}),
                    'input_route_kit_prerequisite': {
                        'schema': 1, 'observed_tick': self.snapshot.tick,
                        'session_id': self.snapshot.session_id,
                        'source': parent['source'], 'source_unit': parent['source_unit'],
                        'layout': parent['layout'], 'state': parent['state'],
                        'kit_item': item, 'kit_inventory_target': count,
                        'kit_kind': 'construction_fuel' if item == 'coal' else 'route_component',
                        'construction_fuel_inventory_target': 15,
                        'parent_local_objective': {
                            'item': focus[0], 'inventory_target': focus[1],
                            'ultimate_goal': self.goal},
                        'parent_planner_item_path': parent_path,
                        'remaining_route_bill': deepcopy(remaining(parent,
                            self._science_reserve() if parent['state'] == 'proposed'
                            else parent['reserve_belts'])),
                    }})
            return plan
        finally:
            self.focus = focus
            self._acquiring_route = previous
            self._economic_acquiring = economic

    def _science_reserve(self) -> int:
        recipe = self.catalog.recipes.get("logistic-science-pack")
        if not recipe:
            return 0
        product = next(p for p in recipe["products"] if p["name"] == "logistic-science-pack")
        per_batch = sum(i["amount"] for i in recipe["ingredients"] if i["name"] == "transport-belt")
        reserve = math.ceil(per_batch * math.ceil(20 / product["amount"]))
        if not 0 <= reserve <= 200:
            raise ValueError("Science reserve exceeds the supported construction budget")
        return reserve

    def _route(self, row, path):
        todo = [s for s in row["steps"] if s["part"] not in row["parts"]]
        if todo:
            self._buffer_service = True
            reserve = self._science_reserve() if row["state"] == "proposed" else row["reserve_belts"]
            bill = remaining(row, reserve)
            for item, count in sorted({**bill, "coal": 15}.items()):
                if self.snapshot.inventory.get(item, 0) < count:
                    previous_parent = getattr(self, '_route_kit_parent', None)
                    self._route_kit_parent = row
                    try:
                        prerequisite = self._acquire(item, count, path)
                    finally:
                        self._route_kit_parent = previous_parent
                    if prerequisite:
                        return prerequisite
            spec = todo[0]
            return self._plan(
                COMMAND, "input_component", parameters={
                    "source": row["source"], "layout": row["layout"], "part": spec["part"],
                    "reserve_belts": reserve,
                    "receipt": f"{self.snapshot.tick}:{row['layout']}:{spec['part']}",
                }, costs=bill, timeout=18000, identity=f"{row['layout']}:{spec['part']}",
                description=f"Build {spec['name']} for {row['source']} input; retain {reserve} science belts",
            )
        for component in ("inserter", "drill"):
            part = row["parts"][component]
            fuel = self.entities[part["role"]].get("fuel", {}).get("coal", 0)
            if fuel < 2 and row["topology"]:
                self._buffer_service = True
                from .fuel_service import service_plan
                return service_plan(self, part["role"], row["source"], path, self._acquire)
        if not flow_complete(row["source"], row["layout"], self.snapshot):
            self._buffer_service = True
            fuel = self._fuel(row["source"], path)
            if fuel:
                return fuel
            return self._wait("input_flow", row["layout"], 3, row["source"], timeout=36000,
                              identity=f"commission:{row['layout']}")
        return None

    def _need(self, item, amount, path=()):
        if self._acquiring_route or self.snapshot.inventory.get(item, 0) >= amount:
            return super()._need(item, amount, path)
        row = sources(self.snapshot).get("recipe:" + item)
        if row and row["state"] != "fault":
            if self.focus is None:
                self._set_focus(item, amount)
            ready = self._ready_buffer_output(item, amount)
            if ready is not None:
                return ready
            proposed = row["state"] == "proposed"
            output = self.factory.get("output_buffers", {}).get("sources", {}).get(row["source"], {})
            recurring = (self.goal == "rocket_launch" and amount - self.snapshot.inventory.get(item, 0) >= 10
                         and self.entities[row["source"]].get("products_finished", 0) >= 20
                         and output_flow_complete(row["source"], output.get("layout", ""), self.snapshot))
            if not proposed or recurring:
                service = self._route(row, path)
                if service:
                    return service
        return super()._need(item, amount, path)

    def _production(self, recipe, role, batches, path):
        row = sources(self.snapshot).get(role)
        if row and len(row["parts"]) == len(row["steps"]):
            # Ore arrives through the physical route, not the player's inventory.
            return self._route(row, path) or self._fuel(role, path)
        return super()._production(recipe, role, batches, path)
