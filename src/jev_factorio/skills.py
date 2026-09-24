"""Fully specified, bounded skill plans over the existing backend operations."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .planning.materials import quantities
from .questions import _candidate_actions
from .state import GameSnapshot
from . import factory_contract

ACTIONS = frozenset({"walk_to_coal", "walk_to_iron", "mine_coal", "mine_iron",
                     "place_burner_drill", "fuel_drill", "craft_stone_furnace", "idle"})
EFFECTS = frozenset({"near", "inventory", "drill_with_output", "drill_fueled", "output"})


def count_entities(snapshot: GameSnapshot, name: str) -> int:
    return sum(e == name or e.startswith(name + "@") for e in snapshot.placed_entities)


@dataclass(frozen=True)
class Step:
    action: str
    effect: str
    item: str = ""
    threshold: float = 0
    costs: dict[str, float] | None = None
    timeout_ticks: int = 600
    parameters: dict | None = None
    verification: dict | None = None

    def __post_init__(self) -> None:
        if (self.action not in ACTIONS | factory_contract.COMMAND_FIELDS.keys()
                or self.effect not in EFFECTS | factory_contract.EFFECTS):
            raise ValueError("Unknown skill action or verifier")
        if self.action in factory_contract.COMMAND_FIELDS:
            factory_contract.validate_command(self.action, self.parameters or {})
        if self.verification is not None:
            if (not isinstance(self.verification, dict)
                    or set(self.verification) - {"role"}
                    or any(not isinstance(value, str) or not value or len(value) > 128
                           for value in self.verification.values())
                    or any(key in (self.parameters or {}) and self.parameters[key] != value
                           for key, value in self.verification.items())):
                raise ValueError("Invalid factory verification identity")
        if (isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float))
                or not math.isfinite(self.threshold)
                or self.threshold < 0 or type(self.timeout_ticks) is not int
                or self.timeout_ticks < 1):
            raise ValueError("Invalid skill bounds")
        quantities(self.costs or {})

    def satisfied(self, snapshot: GameSnapshot) -> bool:
        if self.effect in factory_contract.EFFECTS:
            return factory_contract.satisfied(self.effect, self.item, self.threshold,
                                              {**(self.parameters or {}),
                                               **(self.verification or {})}, snapshot, self.action)
        if self.effect == "near":
            return snapshot.nearby_resources.get(self.item, math.inf) <= 0.5
        if self.effect == "inventory":
            return snapshot.inventory.get(self.item, 0) >= self.threshold
        if self.effect == "drill_with_output":
            return (count_entities(snapshot, "burner-mining-drill") >= self.threshold
                    and snapshot.drill_output_connected is True)
        if self.effect == "drill_fueled":
            return snapshot.drill_fuel > 0
        return snapshot.iron_ore_collected >= self.threshold

    def allowed(self, snapshot: GameSnapshot) -> bool:
        if self.action in factory_contract.COMMAND_FIELDS:
            return (factory_contract.allowed(self.action, self.parameters or {}, snapshot)
                    and factory_contract.launch_readiness.affordable(self.action, self.costs or {}, snapshot)
                    and all(snapshot.inventory.get(item, 0) >= count
                            for item, count in (self.costs or {}).items()))
        if self.action not in _candidate_actions(snapshot):
            return False
        if any(snapshot.inventory.get(k, 0) < v for k, v in (self.costs or {}).items()):
            return False
        # The FLE placement macro consumes a drill AND its output chest.
        if self.action == "place_burner_drill":
            return (snapshot.inventory.get("wooden-chest", 0) >= 1
                    and count_entities(snapshot, "burner-mining-drill") == 0)
        return True


@dataclass(frozen=True)
class Plan:
    id: str
    goal: str
    description: str
    steps: tuple[Step, ...]
    materials: dict | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.goal or not 1 <= len(self.steps) <= 32:
            raise ValueError("Invalid plan")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        return cls(id=data["id"], goal=data["goal"], description=data["description"],
                   steps=tuple(Step(**step) for step in data["steps"]),
                   materials=data.get("materials"))

    def next_step(self, snapshot: GameSnapshot, start: int = 0) -> int:
        while start < len(self.steps) and self.steps[start].satisfied(snapshot):
            start += 1
        return start


def fuel_plans(goal: str, snapshot: GameSnapshot) -> list[Plan]:
    if "coal" not in snapshot.nearby_resources:
        return []
    plans = []
    for target in (5, 10):
        have = snapshot.inventory.get("coal", 0)
        if have >= target:
            continue
        steps = []
        if snapshot.nearby_resources["coal"] > 0.5:
            steps.append(Step("walk_to_coal", "near", "coal"))
        # Existing backend harvest macros collect five units per invocation.
        for amount in range(have + 5, target + 5, 5):
            steps.append(Step("mine_coal", "inventory", "coal", amount))
        plans.append(Plan(f"{goal}:coal:{target}", goal,
                          f"Gather a buffer of at least {target} coal", tuple(steps)))
    return plans


def compile_plans(goal: str, snapshot: GameSnapshot) -> tuple[list[Plan], str]:
    """Expose only capabilities actually executable by this revision.

    A missing rocket skill returns a blocker, not a fictitious build command.
    Future skill compilers can produce the same Plan/Step contracts.
    """
    if goal == "rocket_launch":
        return [], ("Missing full-game production/research/construction skills and "
                    "version-specific native rocket victory telemetry")
    if goal == "stockpile_fuel":
        plans = fuel_plans(goal, snapshot)
    elif goal == "bootstrap_mining":
        if count_entities(snapshot, "burner-mining-drill") == 0:
            if snapshot.inventory.get("coal", 0) < 5:
                plans = fuel_plans(goal, snapshot)
            elif (snapshot.inventory.get("burner-mining-drill", 0) < 1
                  or snapshot.inventory.get("wooden-chest", 0) < 1):
                return [], "Construction requires one burner drill and one output chest"
            elif "iron-ore" not in snapshot.nearby_resources:
                return [], "No observed iron patch; exploration capability is missing"
            else:
                steps = [Step("walk_to_iron", "near", "iron-ore"),
                         Step("place_burner_drill", "drill_with_output", threshold=1,
                              costs={"burner-mining-drill": 1, "wooden-chest": 1})]
                plans = [Plan("build:iron-drill", goal,
                              "Place an iron drill and connect its output chest", tuple(steps))]
        elif snapshot.drill_output_connected is not True:
            return [], "Partial construction: output connection unverified; repair/inspection skill required"
        elif snapshot.drill_fuel <= 0:
            if snapshot.inventory.get("coal", 0) < 5:
                plans = fuel_plans(goal, snapshot)
            else:
                plans = [Plan("fuel:iron-drill", goal, "Fuel the existing iron drill",
                              (Step("fuel_drill", "drill_fueled", costs={"coal": 5}),))]
        else:
            plans = [Plan("verify:iron-output", goal, "Wait for measured ore accumulation",
                          (Step("idle", "output", threshold=max(5, snapshot.iron_ore_collected + 1),
                                timeout_ticks=1800),))]
    else:
        return [], f"No skill compiler for goal {goal}"
    feasible = []
    for plan in plans:
        index = plan.next_step(snapshot)
        if index < len(plan.steps) and plan.steps[index].allowed(snapshot):
            feasible.append(plan)
    return feasible, "" if feasible else "No feasible plan for the observed resources and inventory"
