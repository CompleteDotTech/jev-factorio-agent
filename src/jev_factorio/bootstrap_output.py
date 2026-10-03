"""Current native bootstrap output ownership, never a lifetime ore counter."""
from __future__ import annotations

import math
import re

ROLE = "bootstrap-output:iron-ore"
MODULE = "bootstrap_output_v1"
PROFILE = "e759-observation-v2-water-origin-v4-manual-cycle-v6-connector-observer-v1-bootstrap-output-v1"
MANUAL_CYCLE_PROFILE = "e759-observation-v2-water-origin-v4-manual-cycle-v5-connector-observer-v1-bootstrap-output-v1"
ORIGINS = {"native_paid_bootstrap_placement", "legacy_authorized_current_asset"}


def _integer(value, minimum=0):
    return type(value) is int and minimum <= value <= 2**53 - 1


def _point(value):
    return (isinstance(value, dict) and set(value) == {"x", "y"}
            and all(type(v) in (int, float) and math.isfinite(v) for v in value.values()))


def binding(snapshot, *, allow_pending=False):
    """Only an atomic native ownership row matching the registered endpoint."""
    if (snapshot.world_kind != "fle" or not _integer(snapshot.tick)
            or getattr(snapshot, "_coherent_observation_verified", None) != (snapshot.session_id, snapshot.tick)
            or getattr(snapshot, "_atomic_inventory_verified", None) != (snapshot.session_id, snapshot.tick)):
        return None
    factory = snapshot.factory
    row = factory.get("bootstrap_output")
    runtime = factory.get("acceptance_runtime")
    machine = factory.get("entities", {}).get(ROLE)
    if not all(isinstance(v, dict) for v in (row, runtime, machine)):
        return None
    if (type(row.get("protocol")) is not int or row["protocol"] != 1
            or row.get("role") != ROLE or row.get("origin") not in ORIGINS
            or row.get("session_id") != snapshot.session_id
            or not _integer(row.get("tick")) or row["tick"] != snapshot.tick
            or not isinstance(row.get("binding_id"), str) or not 1 <= len(row["binding_id"]) <= 256
            or not _integer(row.get("bound_at_tick")) or row["bound_at_tick"] > snapshot.tick
            or not _integer(row.get("drill_unit"), 1) or not _integer(row.get("chest_unit"), 1)
            or row["drill_unit"] == row["chest_unit"]
            or any(not _integer(row.get(k), 1) or not _integer(runtime.get(k), 1) or row[k] != runtime.get(k)
                   for k in ("actor_unit", "surface_index", "force_index"))
            or not all(_point(row.get(k)) for k in ("drill_position", "drop_position", "chest_position"))
            or any(abs(row["chest_position"][a] - row["drop_position"][a]) >= .5 for a in ("x", "y"))
            or machine.get("name") != "wooden-chest"
            or not _integer(machine.get("unit_number"), 1) or machine["unit_number"] != row["chest_unit"]
            or machine.get("position") != row["chest_position"]
            or factory.get("drill_output_role") != ROLE
            or factory.get("player_bound") is not True or factory.get("player_connected") is not True
            or row.get("ownership_effective_now") is not True):
        return None
    if type(row.get("native_pending")) is not bool or (row["native_pending"] and not allow_pending):
        return None
    capacity = row.get("capacity")
    if (not isinstance(capacity, dict)
            or set(capacity) != {"schema", "tick", "session_id", "actor_unit", "surface_index",
                                "force_index", "quality", "inventory", "item", "count"}
            or type(capacity.get("schema")) is not int or capacity["schema"] != 1
            or not _integer(capacity.get("tick")) or capacity["tick"] != snapshot.tick
            or capacity.get("session_id") != snapshot.session_id
            or any(not _integer(capacity.get(k), 1) or capacity[k] != row[k]
                   for k in ("actor_unit", "surface_index", "force_index"))
            or capacity.get("quality") != "normal" or capacity.get("inventory") != "character_main"
            or capacity.get("item") != "iron-ore" or not _integer(capacity.get("count"))):
        return None
    stock = row.get("output")
    if (not isinstance(stock, dict) or len(stock) > 64 or stock != machine.get("output")
            or any(not isinstance(k, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,127}", k)
                   or not _integer(v) for k, v in stock.items())
            or stock.get("iron-ore", 0) != snapshot.iron_ore_collected):
        return None
    if row["origin"] == "legacy_authorized_current_asset":
        authorization = row.get("authorization_sha256")
        witness = getattr(snapshot, "_bootstrap_output_ownership_witness", None)
        if (not isinstance(authorization, str) or not re.fullmatch(r"[0-9a-f]{64}", authorization)
                or authorization == "0" * 64
                or row.get("historical_paid_placement_proven") is not False
                or row.get("paid_drill_unit") is not False or row.get("paid_chest_unit") is not False
                or row["binding_id"] != authorization
                or not isinstance(witness, dict)
                or any(not _integer(witness.get(k), 1) for k in (
                    "actor_unit", "surface_index", "force_index", "drill_unit", "chest_unit"))
                or not _integer(witness.get("bound_at_tick"))
                or any(witness.get(k) != row[k] for k in (
                    "session_id", "actor_unit", "surface_index", "force_index", "drill_unit",
                    "chest_unit", "drill_position", "drop_position", "chest_position", "origin",
                    "authorization_sha256", "bound_at_tick"))):
            return None
    elif (row.get("authorization_sha256") is not False
          or row.get("historical_paid_placement_proven") is not True
          or not _integer(row.get("paid_drill_unit"), 1)
          or not _integer(row.get("paid_chest_unit"), 1)
          or row["binding_id"] != f"paid:{row['drill_unit']}:{row['chest_unit']}"
          or row.get("paid_drill_unit") != row["drill_unit"]
          or row.get("paid_chest_unit") != row["chest_unit"]):
        return None
    return row


def allowed(parameters, snapshot):
    row = binding(snapshot)
    if row is None or parameters.get("role") != ROLE or parameters.get("item") != "iron-ore":
        return False
    quantity = parameters.get("quantity")
    headroom = row["capacity"]["count"]
    return (_integer(quantity, 1) and quantity <= 200
            and row["output"].get("iron-ore", 0) >= quantity
            and _integer(headroom) and headroom >= quantity
            and isinstance(parameters.get("receipt"), str)
            and (match := re.fullmatch(r"([0-9]{1,16}):factory_extract:bootstrap-output:iron-ore:iron-ore",
                                       parameters["receipt"])) is not None
            and int(match[1]) <= snapshot.tick)
