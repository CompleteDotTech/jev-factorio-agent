"""Strict contracts for opt-in, inventory-paid furnace output buffers.

A build receipt proves a paid component, not material flow. Commissioning is
separate bounded, sampled conservation evidence, not an anti-tamper guarantee.
"""
from __future__ import annotations

import math

COMMAND = "factory_buffer_build"
FIELDS = {"source", "layout", "part", "receipt"}
PARTS = {"chest": "wooden-chest", "inserter": "burner-inserter"}
EFFECTS = {"buffer_component", "buffer_flow"}


def validate(parameters: dict) -> None:
    if (not isinstance(parameters, dict) or set(parameters) != FIELDS
            or any(not isinstance(value, str) or not value or len(value) > 128
                   for value in parameters.values())
            or parameters["part"] not in PARTS):
        raise ValueError("Invalid furnace-buffer command")


def sources(snapshot) -> dict:
    data = snapshot.factory.get("output_buffers", {})
    if (not isinstance(data, dict) or type(data.get("protocol")) is not int
            or data["protocol"] != 1 or data.get("session_id") != snapshot.session_id
            or type(data.get("tick")) is not int or data["tick"] != snapshot.tick
            or not isinstance(data.get("sources"), dict) or len(data["sources"]) > (5 if "successors" in snapshot.factory else 3)):
        raise ValueError("Missing or stale furnace-buffer telemetry")
    return data["sources"]


def current(row: dict, snapshot) -> bool:
    if not isinstance(row, dict):
        return False
    source = snapshot.factory.get("entities", {}).get(row.get("source", ""), {})
    return (type(row.get("source_unit")) is int and row["source_unit"] > 0
            and source.get("unit_number") == row["source_unit"]
            and row.get("state") in {"proposed", "building", "ready"})


def allowed(parameters: dict, snapshot) -> bool:
    validate(parameters)
    try:
        row = sources(snapshot).get(parameters["source"], {})
    except ValueError:
        return False
    parts = row.get("parts", {})
    return bool(current(row, snapshot) and row.get("layout") == parameters["layout"]
                and not parts.get(parameters["part"])
                and (parameters["part"] == "chest" or parts.get("chest"))
                and snapshot.factory.get("player_bound") is True
                and snapshot.factory.get("player_connected") is True
                and snapshot.factory.get("crafting_queue") == 0
                and snapshot.inventory.get(PARTS[parameters["part"]], 0) >= 1)


def component_complete(parameters: dict, snapshot) -> bool:
    validate(parameters)
    try:
        row = sources(snapshot).get(parameters["source"], {})
    except ValueError:
        return False
    part = row.get("parts", {}).get(parameters["part"], {})
    entity = snapshot.factory.get("entities", {}).get(part.get("role", ""), {})
    return bool(current(row, snapshot) and row.get("layout") == parameters["layout"]
                and part.get("receipt") == parameters["receipt"]
                and part.get("paid") == 1 and type(part.get("paid")) is int
                and type(part.get("unit_number")) is int and part["unit_number"] > 0
                and entity.get("unit_number") == part["unit_number"]
                and entity.get("name") == PARTS[parameters["part"]])


def flow_complete(source: str, layout: str, snapshot) -> bool:
    try:
        row = sources(snapshot).get(source, {})
    except ValueError:
        return False
    if (not current(row, snapshot) or row.get("state") != "ready"
            or row.get("layout") != layout or row.get("topology") is not True):
        return False
    for name, prototype in PARTS.items():
        part = row.get("parts", {}).get(name, {})
        entity = snapshot.factory.get("entities", {}).get(part.get("role", ""), {})
        if (type(part.get("unit_number")) is not int or part["unit_number"] <= 0
                or entity.get("unit_number") != part["unit_number"]
                or entity.get("name") != prototype):
            return False
    proof = row.get("flow", {})
    keys = ("first_tick", "last_tick", "positive_samples", "received")
    if (not isinstance(proof, dict) or any(type(proof.get(key)) is not int
                                         or proof[key] < 0 for key in keys)):
        return False
    return (proof.get("layout") == layout and type(proof.get("source_unit")) is int
            and proof["source_unit"] == row["source_unit"]
            and proof.get("conservation") is True
            and proof["last_tick"] <= snapshot.tick
            and proof["last_tick"] - proof["first_tick"] >= 120
            and proof["positive_samples"] >= 3 and proof["received"] >= 3)


def permits(action: str, parameters: dict, snapshot) -> bool:
    """Never let the actor race the inserter or seed its output evidence."""
    rows = sources(snapshot)
    for source, row in rows.items():
        if row.get("state") == "fault":
            return False
        parts = row.get("parts", {})
        chest = parts.get("chest", {}).get("role")
        role = parameters.get("role")
        if role == chest and action == "factory_insert":
            return False
        if (role == source and parts.get("inserter")
                and action in {"factory_extract", "factory_configure"}):
            return False
        if (role == chest and action == "factory_extract"
                and not flow_complete(source, row.get("layout", ""), snapshot)):
            return False
    return True


def potential(row: dict, snapshot, recipe: dict) -> int:
    """Bound collection by actual outputs and already-paid native ingredients."""
    entity = snapshot.factory["entities"][row["source"]]
    chest = snapshot.factory["entities"].get(row.get("chest_role", ""), {})
    item = row["item"]
    ingredients = recipe.get("ingredients", [])
    if not ingredients or any(entry["type"] != "item" or entry["amount"] <= 0
                              for entry in ingredients):
        return 0
    buffered = min(math.floor(entity.get("input", {}).get(entry["name"], 0) / entry["amount"])
                   for entry in ingredients)
    output = next(entry["amount"] for entry in recipe["products"] if entry["name"] == item)
    return math.floor(chest.get("output", {}).get(item, 0)
                      + entity.get("output", {}).get(item, 0) + row.get("held", 0)
                      + output * (buffered + int(entity.get("crafting") is True)))
