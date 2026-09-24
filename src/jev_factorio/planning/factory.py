"""Bounded native production scheduling, using actual recipes and research costs.

The agent carries solid materials between dedicated machines. Fluids and power
use physical connections. Nothing here creates resources or unlocks research.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict

from ..factory_contract import connected
from ..skills import Plan, Step, compile_plans
from ..state import GameSnapshot
from .catalog import Catalog

RAW_ITEMS = {"coal", "iron-ore", "copper-ore", "stone", "wood"}


class FactoryPlanner:
    def __init__(self, catalog: Catalog, snapshot: GameSnapshot, goal: str,
                 max_expansions: int = 512) -> None:
        self.catalog, self.snapshot, self.goal = catalog, snapshot, goal
        self.factory = snapshot.factory
        self.entities = self.factory.get("entities", {})
        self.researched = snapshot.researched or []
        self.expansions = 0
        self.max_expansions = max_expansions
        self.materials = None

    def _visit(self, key, path):
        self.expansions += 1
        if self.expansions > self.max_expansions:
            raise ValueError("Native production expansion budget exceeded")
        if key in path:
            raise ValueError(f"Native production cycle: {' -> '.join((*path, key))}")
        return (*path, key)

    def _plan(self, action, effect, item="", threshold=0, *, parameters=None,
              verification=None, costs=None, timeout=1800, description="", identity=None):
        parameters = parameters or {}
        key = identity or parameters.get(
            "role", parameters.get("recipe", parameters.get("technology", item))
        )
        if action == 'factory_connect':
            from .connection_identity import connection_key
            key = connection_key(parameters)
        step = Step(action, effect, item, threshold, costs, timeout,
                    parameters=parameters, verification=verification)
        return Plan(f"factory:{action}:{key}", self.goal,
                    description or f"{action}: {key}", (step,), materials=self.materials)

    def _wait(self, effect, item="", threshold=0, role="", timeout=36000, identity=None):
        return self._plan("factory_wait", effect, item, threshold,
                          verification={"role": role} if role else {}, timeout=timeout,
                          description=f"Observe native {effect} progress for {role or item}",
                          identity=identity)

    def _transfer(self, role, item, quantity, extracting=False):
        quantity = min(200, math.ceil(quantity))
        action = "factory_extract" if extracting else "factory_insert"
        receipt = f"{self.snapshot.tick}:{action}:{role}:{item}"
        return self._plan(
            action, "transfer", parameters={"role": role, "item": item,
                                           "quantity": quantity, "receipt": receipt},
            costs={} if extracting else {item: quantity},
            description=f"{'Collect' if extracting else 'Deliver'} {quantity} {item} "
                        f"{'from' if extracting else 'to'} {role}",
        )

    def _recipe(self, item, path):
        recipe = self.catalog.recipe_for(item)
        if not self.catalog.enabled(recipe, self.researched):
            unlocks = self.catalog.unlocks(recipe["name"])
            if not unlocks:
                raise ValueError(f"No native technology unlocks {recipe['name']}")
            return recipe, self._research(unlocks[0], path)
        return recipe, None

    def _fair_resource_identity(self, item: str, target: int) -> str | None:
        """Bind failures to a native resource site, including any replacement there."""
        targets = self.factory.get("fair_resource_targets")
        evidence = targets.get(item) if isinstance(targets, dict) else None
        if not isinstance(evidence, dict):
            return None
        name, surface_index = evidence.get("name"), evidence.get("surface_index")
        position = evidence.get("position")
        if (not isinstance(name, str) or not name.strip()
                or (item != "wood" and name != item)
                or type(surface_index) is not int or surface_index <= 0
                or not isinstance(position, dict)):
            return None
        coordinates = [position.get(axis) for axis in ("x", "y")]
        if any(type(value) not in {int, float} or not math.isfinite(value)
               for value in coordinates):
            return None
        site = {
            "name": name, "surface_index": surface_index,
            "position": {
                axis: float(value) if value else 0.0
                for axis, value in zip(("x", "y"), coordinates)
            },
        }
        identity = json.dumps(site, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return f"{item}:target:{target}:site:{identity}"

    def _output_pickup(self, item, missing):
        candidates = []
        for role, machine in sorted(self.entities.items()):
            if 'successors' in self.factory:
                from ..successors import private_output
                if private_output(role, self.snapshot):
                    continue  # Trial or preferred output needs the successor-aware planner.
            available = machine.get("output", {}).get(item, 0)
            if available:
                candidates.append((role, machine, min(200, missing, available)))
        if not candidates:
            return None
        if len(candidates) > 1:
            eligible = [candidate for candidate in candidates
                        if self._transfer(candidate[0], item, candidate[2], extracting=True)
                        .steps[0].allowed(self.snapshot)]
            # If none pass, retain the old first choice for the unchanged
            # fail-closed execution boundary rather than inventing new supply.
            candidates = eligible or candidates[:1]
            from .service_policy import position
            from .scheduling import SERVICE_TICKS, TRAVEL_TICKS_PER_TILE

            origin = position(self.snapshot.player_position)
            locations = [position(machine.get("position")) for _, machine, _ in candidates]
            # Unknown geometry retains the existing deterministic role order.
            # These policy estimates rank observed stock, never forecast output
            # or authorize a transfer without its normal fresh native checks.
            if origin is not None and all(location is not None for location in locations):
                def rank(entry):
                    (role, _, useful), location = entry
                    distance = sum(abs(a - b) for a, b in zip(origin, location))
                    ticks = SERVICE_TICKS + distance * TRAVEL_TICKS_PER_TILE
                    return ticks / useful, -useful, distance, role

                candidates = [min(zip(candidates, locations), key=rank)[0]]
        role, _, quantity = candidates[0]
        return self._transfer(role, item, quantity, extracting=True)

    def _need(self, item, amount, path=()):
        have = self.snapshot.inventory.get(item, 0)
        if have >= amount:
            return None
        path = self._visit("item:" + item, path)
        missing = math.ceil(amount - have)
        pickup = self._output_pickup(item, missing)
        if pickup:
            return pickup
        if item in RAW_ITEMS:
            if item not in self.snapshot.nearby_resources:
                return self._explore(item)
            # A wood observation proves only one currently mineable tree, not
            # a whole forest. A tree can yield several wood and then disappear,
            # unlike the stackable resource patches used for ore, coal, and
            # stone. Keep wood to one fair native mine; the next observation
            # chooses any later tree normally.
            quantity = 1 if item == "wood" else min(50, missing)
            target = have + quantity
            identity = self._fair_resource_identity(item, target)
            if identity is None:
                return self._explore(item)
            return self._plan(
                "factory_gather", "inventory", item, target,
                parameters={"resource": item, "quantity": quantity}, timeout=18000,
                description=f"Gather {quantity} observed {item}; inventory target {target}",
                identity=identity,
            )
        recipe, prerequisite = self._recipe(item, path)
        if prerequisite:
            return prerequisite
        if self.materials is None:
            self.materials = asdict(self.catalog.material_plan(
                item, amount, self.snapshot.inventory, self.researched
            ))
        output = next(product["amount"] for product in recipe["products"]
                      if product["name"] == item)
        batches = min(20, math.ceil(missing / output))
        if self.catalog.hand_categories.get(recipe["category"]) and all(
            ingredient["type"] == "item" for ingredient in recipe["ingredients"]
        ):
            costs = {ingredient["name"]: math.ceil(ingredient["amount"] * batches)
                     for ingredient in recipe["ingredients"]}
            for ingredient, count in costs.items():
                prerequisite = self._need(ingredient, count, path)
                if prerequisite:
                    return prerequisite
            return self._plan(
                "factory_craft", "inventory", item, have + output * batches,
                parameters={"recipe": recipe["name"], "batches": batches}, costs=costs,
                timeout=max(1800, math.ceil(recipe["energy"] * batches * 120)),
                description=f"Native hand-craft {batches} batches of {recipe['name']}",
            )
        role = "recipe:" + recipe["name"]
        prerequisite = self._production(recipe, role, batches, path)
        if prerequisite:
            return prerequisite
        return self._wait("machine_output", item, min(missing, output), role,
                          timeout=max(3600, math.ceil(recipe["energy"] * 1200)))

    def _explore(self, resource):
        radius = self.factory.get("exploration_radius", 8)
        if radius >= 32:
            raise ValueError(f"No {resource} observed within the bounded exploration area")
        return self._plan("factory_explore", "explored", threshold=radius + 4,
                          parameters={"radius": radius + 4}, timeout=18000,
                          description=f"Generate normal terrain out to {radius + 4} chunks")

    def _machine(self, role, name, path, anchor="factory"):
        if role in self.entities:
            if self.entities[role]["name"] != name:
                raise ValueError(f"Unexpected native entity occupies {role}")
            return None
        if anchor in {"water", "crude-oil"} and anchor not in self.snapshot.nearby_resources:
            return self._explore(anchor)
        prerequisite = self._need(name, 1, path)
        if prerequisite:
            return prerequisite
        return self._plan("factory_place", "machine",
                          parameters={"role": role, "name": name, "anchor": anchor},
                          costs={name: 1}, description=f"Place one {name} for {role}")

    def _connect(self, source, target, kind, fluid, path):
        if connected(self.factory, source, target, kind, fluid):
            return None
        start, end = self.entities[source]["position"], self.entities[target]["position"]
        distance = abs(start["x"] - end["x"]) + abs(start["y"] - end["y"])
        budget = math.ceil(distance / (5 if kind == "small-electric-pole" else 1)) + 30
        if budget > 1200:
            raise ValueError("Physical connection exceeds the bounded construction budget")
        prerequisite = self._need(kind, budget, path)
        if prerequisite:
            return prerequisite
        return self._plan(
            "factory_connect", "connection",
            parameters={"source": source, "target": target, "kind": kind, "fluid": fluid},
            costs={kind: budget}, timeout=18000,
            description=f"Physically connect {source} to {target} using {kind} ({fluid})",
        )

    def _fuel(self, role, path):
        current = self.entities[role].get("fuel", {}).get("coal", 0)
        target = min(50, self.catalog.stack_sizes.get("coal", 50))
        if current >= min(5, target):
            return None
        needed = target - current
        prerequisite = self._need("coal", needed, path)
        return prerequisite or self._transfer(role, "coal", needed)

    def _power(self, path):
        path = self._visit("infrastructure:power", path)
        for role, name, anchor in (
            ("utility:water", "offshore-pump", "water"),
            ("utility:boiler", "boiler", "utility:water"),
            ("utility:engine", "steam-engine", "utility:boiler"),
        ):
            prerequisite = self._machine(role, name, path, anchor)
            if prerequisite:
                return prerequisite
        prerequisite = self._connect("utility:water", "utility:boiler", "pipe", "water", path)
        if prerequisite:
            return prerequisite
        prerequisite = self._connect("utility:boiler", "utility:engine", "pipe", "steam", path)
        return prerequisite or self._fuel("utility:boiler", path)

    def _powered(self, role, path):
        prerequisite = self._power(path)
        return prerequisite or self._connect(
            "utility:engine", role, "small-electric-pole", "electricity", path
        )

    def _machine_type(self, recipe):
        preferences = {
            "smelting": ["stone-furnace"],
            "oil-processing": ["oil-refinery"],
            "chemistry": ["chemical-plant"],
            "rocket-building": ["rocket-silo"],
            "crafting-with-fluid": ["assembling-machine-2"],
        }
        choices = preferences.get(recipe["category"], ["assembling-machine-1", "assembling-machine-2"])
        for name in choices:
            if self.catalog.machines.get(name, {}).get("categories", {}).get(recipe["category"]):
                return name
        raise ValueError(f"No native machine supports recipe category {recipe['category']}")

    def _production(self, recipe, role, batches, path):
        for ingredient in recipe["ingredients"]:
            if ingredient["type"] == "item":
                stack_size = self.catalog.stack_sizes.get(ingredient["name"], 200)
                batches = min(batches, max(1, math.floor(stack_size / ingredient["amount"])))
        name = self._machine_type(recipe)
        prerequisite = self._machine(role, name, path)
        if prerequisite:
            return prerequisite
        machine = self.entities[role]
        if name not in {"stone-furnace", "steel-furnace", "electric-furnace"} and (
            machine.get("recipe") != recipe["name"]
        ):
            return self._plan(
                "factory_configure", "machine_recipe",
                parameters={"role": role, "recipe": recipe["name"]},
                description=f"Set {role} to the unlocked {recipe['name']} recipe",
            )
        if self.catalog.machines[name]["electric"]:
            prerequisite = self._powered(role, path)
            if prerequisite:
                return prerequisite
        elif self.catalog.machines[name]["burner"]:
            prerequisite = self._fuel(role, path)
            if prerequisite:
                return prerequisite
        for ingredient in recipe["ingredients"]:
            item = ingredient["name"]
            if ingredient["type"] == "fluid":
                source, prerequisite = self._fluid(item, path)
                if prerequisite:
                    return prerequisite
                prerequisite = self._connect(source, role, "pipe", item, path)
                if prerequisite:
                    return prerequisite
            else:
                buffered = machine.get("input", {}).get(item, 0)
                in_flight = ingredient["amount"] if machine.get("crafting") else 0
                needed = max(0, math.ceil(ingredient["amount"] * batches - buffered - in_flight))
                if needed:
                    prerequisite = self._need(item, needed, path)
                    return prerequisite or self._transfer(role, item, needed)
        return None

    def _fluid(self, item, path):
        path = self._visit("fluid:" + item, path)
        if item == "water":
            return "utility:water", self._machine("utility:water", "offshore-pump", path, "water")
        if item == "crude-oil":
            if item not in self.snapshot.nearby_resources:
                return "", self._explore(item)
            role = "utility:pumpjack"
            prerequisite = self._machine(role, "pumpjack", path, "crude-oil")
            return role, prerequisite or self._powered(role, path)
        recipe, prerequisite = self._recipe(item, path)
        role = "recipe:" + recipe["name"]
        if prerequisite:
            return role, prerequisite
        prerequisite = self._production(recipe, role, 1, path)
        if prerequisite:
            return role, prerequisite
        fluid_products = [product for product in recipe["products"] if product["type"] == "fluid"]
        if len(fluid_products) > 1:
            for product in fluid_products:
                tank = "buffer:" + recipe["name"] + ":" + product["name"]
                prerequisite = self._machine(tank, "storage-tank", path, role)
                if prerequisite:
                    return role, prerequisite
                prerequisite = self._connect(role, tank, "pipe", product["name"], path)
                if prerequisite:
                    return role, prerequisite
        return role, None

    def _research(self, name, path=()):
        if name in self.researched:
            return None
        path = self._visit("technology:" + name, path)
        tech = self.catalog.technologies.get(name)
        if tech is None or not tech["enabled"]:
            raise ValueError(f"Native technology {name} is unavailable")
        for parent in tech["prerequisites"]:
            prerequisite = self._research(parent, path)
            if prerequisite:
                return prerequisite
        trigger = tech.get("trigger")
        if trigger:
            if trigger["type"] == "craft-item":
                item = trigger["item"]["name"]
                produced = self.factory.get("produced", {}).get(item, 0)
                count = trigger.get("count", 1)
                if produced < count:
                    target = self.snapshot.inventory.get(item, 0) + min(20, count - produced)
                    prerequisite = self._need(item, target, path)
                    if prerequisite:
                        return prerequisite
                return self._wait("researched", name, timeout=1800)
            if trigger["type"] == "mine-entity" and trigger["entity"] == "crude-oil":
                _, prerequisite = self._fluid("crude-oil", path)
                return prerequisite or self._wait("researched", name, timeout=3600)
            raise ValueError(f"Unsupported native research trigger: {trigger['type']}")
        prerequisite = self._machine("utility:lab", "lab", path)
        if prerequisite:
            return prerequisite
        prerequisite = self._powered("utility:lab", path)
        if prerequisite:
            return prerequisite
        current = self.factory.get("research", "")
        if current and current != name:
            return self._research(current, path)
        if not current:
            return self._plan("factory_research", "research_started", name,
                              parameters={"technology": name},
                              description=f"Start native research: {name}")
        lab = self.entities["utility:lab"]
        for ingredient in tech["ingredients"]:
            item = ingredient["name"]
            if lab.get("input", {}).get(item, 0) < ingredient["amount"]:
                needed = min(20, math.ceil(
                    tech["count"] * (1 - self.factory.get("research_progress", 0))
                    * ingredient["amount"]
                ))
                prerequisite = self._need(item, max(1, needed), path)
                return prerequisite or self._transfer("utility:lab", item, max(1, needed))
        progress = self.factory.get("research_progress", 0)
        increment = min(0.01, 1 / max(1, tech["count"]))
        # A wait which timed out while the lab lacked a pack must not veto a
        # later wait after observed research progress or lab supplies changed.
        # Retain the old failure record, but bind this passive observation to
        # the exact progress/supply epoch rather than just the technology.
        supplies = ",".join(
            f"{ingredient['name']}={lab.get('input', {}).get(ingredient['name'], 0)}"
            for ingredient in sorted(tech["ingredients"], key=lambda value: value["name"])
        )
        # repr(float) is the shortest round-trippable spelling, so distinct
        # native progress values cannot collapse into the same failure budget.
        identity = f"{name}:progress:{progress!r}:supplies:{supplies}"
        return self._wait("research_progress", name, min(1, progress + increment),
                          timeout=max(3600, min(216000, tech["energy_ticks"] * 4)),
                          identity=identity)

    def plan(self) -> Plan | None:
        if not self.factory:
            raise ValueError("Native factory telemetry is unavailable")
        if self.factory.get("player_connected") is not True:
            raise ValueError("Native progression requires a connected game client")
        if not self.factory.get("player_bound"):
            return self._plan("factory_bind", "player_bound",
                              description="Bind the existing viewer to the existing agent for native crafting")
        if self.factory.get("crafting_queue", 0):
            return self._wait("crafting_idle")
        if "utility:boiler" in self.entities:
            prerequisite = self._fuel("utility:boiler", ())
            if prerequisite:
                return prerequisite
        if self.goal == "iron_smelting":
            return self._need("iron-plate", 10)
        if self.goal == "automation_science":
            return self._need("automation-science-pack", 10)
        if self.goal == "steam_power":
            prerequisite = self._machine("utility:lab", "lab", ())
            return (prerequisite or self._powered("utility:lab", ())
                    or self._wait("powered", role="utility:lab", timeout=3600))
        if self.goal != "rocket_launch":
            raise ValueError(f"No native campaign target: {self.goal}")
        prerequisite = self._research("rocket-silo")
        if prerequisite:
            return prerequisite
        from .launch import prerequisite as launch_prerequisite
        preparation = launch_prerequisite(self)
        if preparation:
            return preparation
        recipe = self.catalog.recipes["rocket-part"]
        role = "recipe:rocket-part"
        silo = self.entities.get(role, {})
        if silo.get("rocket_ready"):
            return self._plan("factory_launch", "rocket_launched",
                              parameters={"role": role}, timeout=18000,
                              description="Launch the completed native rocket and observe the force counter")
        required = silo["parts_required"] if silo else 100
        if silo.get("rocket_parts", 0) >= required:
            return self._wait("rocket_ready", role=role, timeout=18000)
        remaining = max(1, required - silo.get("rocket_parts", 0))
        prerequisite = self._production(recipe, role, min(10, remaining), ())
        return prerequisite or self._wait("rocket_parts", threshold=silo.get("rocket_parts", 0) + 1,
                                          role=role, timeout=36000)


def compile_factory(goal: str, snapshot: GameSnapshot, catalog: Catalog) -> tuple[list[Plan], str]:
    try:
        if goal == "bootstrap_mining":
            role = snapshot.factory.get("drill_output_role")
            if (role and snapshot.drill_output_connected
                    and snapshot.drill_status == "waiting_for_space_in_destination"):
                available = snapshot.factory["entities"][role].get("output", {}).get("iron-ore", 0)
                if available:
                    planner = FactoryPlanner(catalog, snapshot, goal)
                    return [planner._transfer(role, "iron-ore", min(50, available), extracting=True)], ""
            return compile_plans(goal, snapshot)
        plan = FactoryPlanner(catalog, snapshot, goal).plan()
        return ([plan] if plan else []), "" if plan else "No remaining native production action"
    except (ValueError, KeyError) as error:
        return [], str(error)
