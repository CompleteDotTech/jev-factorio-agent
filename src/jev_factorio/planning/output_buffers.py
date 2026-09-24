"""Small furnace-output cells, not a general belt/layout planner."""
from __future__ import annotations

import math
from dataclasses import replace

from ..output_buffers import COMMAND, PARTS, flow_complete, potential, sources
from ..skills import Plan
from .ready_work import ReadyWorkPlanner
from .service_visits import service_visit


class OutputBufferPlanner(ReadyWorkPlanner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._acquiring_buffer = False
        self._buffer_service = False

    def _prerequisite(self, item: str, count: int, path) -> Plan | None:
        self._acquiring_buffer = True
        try:
            return super()._need(item, count, path)
        finally:
            self._acquiring_buffer = False

    def _buffer(self, row: dict, amount: int, path) -> Plan | None:
        parts = row.get("parts", {})
        # Reserve by committing one bounded prerequisite at a time. Do not
        # offer sibling alternatives that might consume the construction kit.
        for part, name in PARTS.items():
            if part not in parts:
                self._buffer_service = True
                for material, count in ((name, 1), ("coal", 5)):
                    prerequisite = self._prerequisite(material, count, path)
                    if prerequisite:
                        return prerequisite
                receipt = f"buffer:{self.snapshot.tick}:{row['source_unit']}:{part}"
                return self._plan(
                    COMMAND, "buffer_component", parameters={
                        "source": row["source"], "layout": row["layout"],
                        "part": part, "receipt": receipt,
                    }, costs={name: 1}, identity=f"{row['layout']}:{part}",
                    description=f"Build paid {name} for {row['source']} output buffer",
                    timeout=18000,
                )
        inserter_role = parts["inserter"]["role"]
        inserter = self.entities[inserter_role]
        fuel = inserter.get("fuel", {}).get("coal", 0)
        if fuel < 2:
            self._buffer_service = True
            count = 5 - fuel
            prerequisite = self._prerequisite("coal", count, path)
            return prerequisite or self._transfer(inserter_role, "coal", count)
        if not flow_complete(row["source"], row["layout"], self.snapshot):
            self._buffer_service = True
            # A short, explicit commissioning interval. Never call placement
            # success proof of transport; all three native growth samples count.
            return self._wait("buffer_flow", row["layout"], 3, row["source"],
                              timeout=1800, identity=f"commission:{row['layout']}")
        return None

    def _need(self, item, amount, path=()):
        if self._acquiring_buffer or self.snapshot.inventory.get(item, 0) >= amount:
            return super()._need(item, amount, path)
        if self.focus is None:
            self._set_focus(item, amount)
        for row in sources(self.snapshot).values():
            if row.get("item") != item or row.get("state") == "fault":
                continue
            machine = self.entities[row["source"]]
            recipe = self.catalog.recipes.get(item, {})
            if not recipe:
                continue
            if row["state"] == "proposed" and (
                self.goal != "rocket_launch" or amount - self.snapshot.inventory.get(item, 0) < 10
                or machine.get("products_finished", 0) < 20
                or machine.get("fuel", {}).get("coal", 0) < 5
                or potential(row, self.snapshot, recipe) < 10
            ):
                continue  # Do not build infrastructure for a one-off bootstrap item.
            service = self._buffer(row, amount, path)
            if service:
                return service
            missing = math.ceil(amount - self.snapshot.inventory.get(item, 0))
            available = self.entities[row["chest_role"]].get("output", {}).get(item, 0)
            incoming = potential(row, self.snapshot, recipe)
            target = min(self.collection_batch, missing, incoming)
            if available and available >= target:
                return self._transfer(row["chest_role"], item, min(missing, available), extracting=True)
            if incoming:
                # Refuel the producer when its in-flight inventory depends on it.
                if machine.get("fuel", {}).get("coal", 0) < 5:
                    prerequisite = self._fuel(row["source"], path)
                    if prerequisite:
                        return prerequisite
                return self._wait("machine_output", item, max(1, target), row["chest_role"],
                                  timeout=7200, identity=f"collect:{row['layout']}:{target}")
            output = next(entry["amount"] for entry in recipe["products"] if entry["name"] == item)
            prerequisite = self._production(recipe, row["source"], min(20, math.ceil(missing / output)), path)
            return prerequisite or self._wait("machine_output", item, min(10, missing), row["chest_role"],
                                              timeout=7200, identity=f"collect:{row['layout']}:{missing}")
        return super()._need(item, amount, path)

    def candidates(self) -> list[Plan]:
        primary = self.plan()
        if primary is None:
            return []
        if (self._buffer_service or not self.focus or self.factory.get("crafting_queue", 0)
                or primary.steps[0].action not in {
                    "factory_gather", "factory_insert", "factory_extract", "factory_wait"
                }):
            return [primary] if self._buffer_service else [service_visit(self, primary)]
        candidates = [primary]
        for item, amount in list(sorted(self.targets.items()))[:32]:
            if self.snapshot.inventory.get(item, 0) >= amount:
                continue
            worker = self._candidate_worker()
            try:
                candidate = worker._need(item, amount)
            except (KeyError, ValueError):
                continue
            if candidate and not worker._buffer_service and candidate.steps[0].action in {
                "factory_gather", "factory_insert", "factory_extract", "factory_craft"
            }:
                candidates.append(candidate)
        unique = {}
        for candidate in candidates:
            if candidate.steps[0].allowed(self.snapshot) and not candidate.steps[0].satisfied(self.snapshot):
                unique.setdefault(candidate.id, candidate)
        ready = [candidate for candidate in unique.values() if candidate.steps[0].action != "factory_wait"]
        item, amount = self.focus
        return [service_visit(self, replace(candidate, description=f"Next production batch: {amount} {item}. "
                        + candidate.description)) for candidate in (ready or list(unique.values()))[:self.max_candidates]]
