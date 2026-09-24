"""Contracts for owned drill/belt/furnace routes; placement is not flow proof."""
from __future__ import annotations

import math

COMMAND = "factory_input_build"
FIELDS = {"source", "layout", "part", "receipt", "reserve_belts"}
EFFECTS = {"input_component", "input_flow"}
ORES = {"recipe:iron-plate": "iron-ore", "recipe:copper-plate": "copper-ore",
        "growth:iron-plate": "iron-ore", "growth:copper-plate": "copper-ore"}
MAX_BELTS = 64


def _text(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128


def _integer(value, low=0, high=2**53 - 1) -> bool:
    return type(value) is int and low <= value <= high


def validate(parameters: dict) -> None:
    if (not isinstance(parameters, dict) or set(parameters) != FIELDS
            or any(not _text(parameters[key]) for key in FIELDS - {"reserve_belts"})
            or parameters["source"] not in ORES
            or not _integer(parameters["reserve_belts"], 0, 200)):
        raise ValueError("Invalid input-route command")
    part = parameters["part"]
    if part not in {"drill", "inserter"} and part not in {
        f"belt:{index}" for index in range(1, MAX_BELTS + 1)
    }:
        raise ValueError("Invalid input-route component")


def sources(snapshot) -> dict:
    data = snapshot.factory.get("input_routes")
    if (not isinstance(data, dict) or not _integer(data.get("protocol"), 1, 1)
            or data.get("session_id") != snapshot.session_id
            or not _integer(data.get("tick")) or data["tick"] != snapshot.tick
            or not isinstance(data.get("sources"), dict) or len(data["sources"]) > (4 if "successors" in snapshot.factory else 2)):
        raise ValueError("Missing or stale input-route telemetry")
    for source, row in data["sources"].items():
        if (source not in ORES or source.startswith("growth:") and "successors" not in snapshot.factory
                or not isinstance(row, dict) or row.get("source") != source
                or row.get("ore") != ORES[source] or row.get("item") != source[7:]
                or not _integer(row.get("source_unit"), 1) or not _text(row.get("layout"))
                or row.get("state") not in {"proposed", "building", "ready", "fault"}
                or type(row.get("topology")) is not bool
                or not _integer(row.get("reserve_belts"), 0, 200)
                or not isinstance(row.get("steps"), list)
                or not 3 <= len(row["steps"]) <= MAX_BELTS + 2
                or not isinstance(row.get("parts"), dict)
                or not isinstance(row.get("flow"), dict)):
            raise ValueError("Invalid input-route row")
        steps = row["steps"]
        expected = ["inserter", *[f"belt:{i}" for i in range(len(steps)-2, 0, -1)], "drill"]
        if [step.get("part") for step in steps if isinstance(step, dict)] != expected:
            raise ValueError("Invalid downstream-first construction order")
        positions = set()
        for step in steps:
            part, point = step["part"], step.get("position")
            name = ("burner-inserter" if part == "inserter" else
                    "burner-mining-drill" if part == "drill" else "transport-belt")
            if (step.get("name") != name or not isinstance(point, dict)
                    or set(point) != {"x", "y"}
                    or any(type(v) not in {int, float} or not math.isfinite(v)
                           or abs(v) > 1_000_000 for v in point.values())
                    or type(step.get("direction")) is not int
                    or step["direction"] not in {0, 4, 8, 12}):
                raise ValueError("Invalid input-route geometry")
            coordinate = (point["x"], point["y"])
            fraction = 0 if part == "drill" else 0.5
            if coordinate in positions or any(value % 1 != fraction for value in coordinate):
                raise ValueError("Overlapping or off-grid input-route geometry")
            positions.add(coordinate)
        if (list(row["parts"]) and not set(row["parts"]).issubset(expected)):
            raise ValueError("Unknown paid input component")
        # Only a contiguous paid prefix is valid, including after response loss.
        prefix = expected[:len(row["parts"])]
        if set(prefix) != set(row["parts"]):
            raise ValueError("Non-contiguous input-route receipt prefix")
        if ((row["state"] == "proposed" and (row["parts"] or row["topology"]))
                or (row["state"] == "ready" and (len(row["parts"]) != len(steps) or not row["topology"]))):
            raise ValueError("Inconsistent input-route phase")
        units, roles, receipts = {row["source_unit"]}, set(), set()
        for part in row["parts"].values():
            if (not isinstance(part, dict) or not _text(part.get("role"))
                    or not _text(part.get("receipt")) or not _integer(part.get("unit_number"), 1)
                    or not _integer(part.get("paid"), 1, 1)
                    or part["unit_number"] in units or part["role"] in roles
                    or part["receipt"] in receipts):
                raise ValueError("Invalid paid input-route component")
            units.add(part["unit_number"])
            roles.add(part["role"])
            receipts.add(part["receipt"])
    return data["sources"]


def current(row: dict, snapshot) -> bool:
    entities = snapshot.factory.get("entities", {})
    source = entities.get(row.get("source"), {})
    if (not _integer(source.get("unit_number"), 1)
            or source.get("name") != "stone-furnace"
            or source.get("unit_number") != row.get("source_unit")
            or row.get("state") == "fault"):
        return False
    for step in row["steps"]:
        receipt = row["parts"].get(step["part"])
        if receipt:
            entity = entities.get(receipt["role"], {})
            if (entity.get("unit_number") != receipt["unit_number"]
                    or entity.get("name") != step["name"]
                    or entity.get("position") != step["position"]):
                return False
    return True


def remaining(row: dict, reserve_belts: int | None = None) -> dict[str, int]:
    bill: dict[str, int] = {}
    for step in row["steps"]:
        if step["part"] not in row["parts"]:
            bill[step["name"]] = bill.get(step["name"], 0) + 1
    keep = row["reserve_belts"] if reserve_belts is None else reserve_belts
    if keep:
        bill["transport-belt"] = bill.get("transport-belt", 0) + keep
    return bill


def allowed(parameters: dict, snapshot) -> bool:
    validate(parameters)
    try:
        row = sources(snapshot).get(parameters["source"])
        if not row or not current(row, snapshot) or row["layout"] != parameters["layout"]:
            return False
        todo = [s for s in row["steps"] if s["part"] not in row["parts"]]
        return bool(todo and todo[0]["part"] == parameters["part"]
                    and (row["state"] == "proposed" or row["reserve_belts"] == parameters["reserve_belts"])
                    and all(snapshot.inventory.get(k, 0) >= v
                            for k, v in remaining(row, parameters["reserve_belts"]).items())
                    and snapshot.factory.get("player_bound") is True
                    and snapshot.factory.get("player_connected") is True
                    and snapshot.factory.get("crafting_queue") == 0)
    except (ValueError, KeyError, TypeError):
        return False


def component_complete(parameters: dict, snapshot) -> bool:
    validate(parameters)
    try:
        row = sources(snapshot).get(parameters["source"])
        part = row["parts"].get(parameters["part"], {}) if row else {}
        return bool(row and current(row, snapshot) and row["layout"] == parameters["layout"]
                    and row["reserve_belts"] == parameters["reserve_belts"]
                    and part.get("receipt") == parameters["receipt"])
    except (ValueError, KeyError, TypeError):
        return False


def flow_complete(source: str, layout: str, snapshot) -> bool:
    try:
        row = sources(snapshot).get(source)
        if (not row or not current(row, snapshot) or row["layout"] != layout
                or row["state"] != "ready" or row["topology"] is not True
                or len(row["parts"]) != len(row["steps"])):
            return False
        flow = row["flow"]
        numeric = ("first_tick", "last_tick", "positive_samples", "received", "mined", "delivered", "new_plates")
        return (all(_integer(flow.get(key)) for key in numeric)
                and flow.get("layout") == layout and flow.get("source_unit") == row["source_unit"]
                and type(flow.get("source_unit")) is int and flow.get("conservation") is True
                and flow["last_tick"] <= snapshot.tick and flow["last_tick"]-flow["first_tick"] >= 120
                and all(flow[key] >= 3 for key in numeric[2:]))
    except (ValueError, KeyError, TypeError):
        return False


def permits(action: str, parameters: dict, snapshot) -> bool:
    """No manual ore injection, route theft, or collection before commissioning."""
    for source, row in sources(snapshot).items():
        if not current(row, snapshot):
            return False
        full = len(row["steps"]) == len(row["parts"])
        role = parameters.get("role")
        component_roles = {part["role"] for part in row["parts"].values()}
        if role in component_roles and action in {"factory_insert", "factory_extract", "factory_configure"}:
            fuel_roles = {row["parts"].get(part, {}).get("role") for part in ("drill", "inserter")}
            if not (full and row["topology"] and action == "factory_insert"
                    and role in fuel_roles and parameters.get("item") == "coal"):
                return False
        if full:
            if role == source and action in {"factory_insert", "factory_configure"}:
                if action != "factory_insert" or parameters.get("item") != "coal":
                    return False
            if source.startswith("recipe:") and action == "factory_gather" and parameters.get("resource") == row["ore"]:
                return False
            output = snapshot.factory.get("output_buffers", {}).get("sources", {}).get(source, {})
            if (action == "factory_extract" and role == output.get("chest_role")
                    and not flow_complete(source, row["layout"], snapshot)):
                return False
    return True
