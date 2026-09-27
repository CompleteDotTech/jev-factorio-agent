"""Bounded downstream admission over the one paid-corridor contract.

Configured intents are an allowlist, not construction orders. Current research,
owned supply, native recipe costs and labelled service estimates decide whether
to offer investment. A marker affects ranking only; fresh dispatch re-evaluates
this policy and the ordinary native/receipt guards still authorize the mutation.
"""
from __future__ import annotations

from dataclasses import replace
import math
from statistics import median

from .. import solid_routes as contract
from ..skills import Plan
from ..telemetry import validate_attempt
from .demand import SupplyLedger
from .solid_routes import candidates as build_candidates
from .scheduling import SERVICE_TICKS, TRAVEL_TICKS_PER_TILE

MARKER = "solid_investment"
HORIZON_PACKS = 120
MAX_OFFERS = 2
MAX_SERVICE_TICKS = 216_000
MAX_HISTORY = 64
PACKS = frozenset({"automation-science-pack", "logistic-science-pack"})
# Actual endpoint recipes are additionally constrained by native qualification.
RECIPES = PACKS | {"iron-gear-wheel", "inserter", "transport-belt",
                   "electronic-circuit", "copper-cable"}


def finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2**53 - 1


def requirements(snapshot, catalog, *, reserved=None, job=None) -> tuple[dict, str]:
    """A current research horizon, not inferred progress or an unbounded rocket BOM."""
    tech = catalog.technologies.get(snapshot.factory.get("research", ""), {})
    progress = snapshot.factory.get("research_progress")
    if (not tech or tech.get("trigger") or not finite(progress) or progress >= 1
            or not finite(tech.get("count")) or tech["count"] == 0
            or snapshot.factory.get("research") in (snapshot.researched or [])):
        return {}, "current_research_unknown_or_complete"
    ingredients = tech.get("ingredients")
    if not isinstance(ingredients, list) or not 1 <= len(ingredients) <= 8:
        return {}, "unsupported_research_ingredients"
    lab = snapshot.factory.get("entities", {}).get("utility:lab", {})
    if (lab.get("name") != "lab" or not contract.integer(lab.get("unit_number"), 1)
            or not finite(lab.get("energy")) or lab["energy"] <= 0):
        return {}, "research_delivery_unavailable"
    roots = {}
    for ingredient in ingredients:
        if (not isinstance(ingredient, dict) or ingredient.get("name") not in PACKS
                or ingredient["name"] in roots or not finite(ingredient.get("amount"))
                or ingredient["amount"] == 0):
            return {}, "unsupported_research_ingredients"
        name = ingredient["name"]
        stock = lab.get("input", {}).get(name, 0)
        if not finite(stock):
            return {}, "invalid_science_stock"
        roots[name] = min(HORIZON_PACKS, max(0, math.ceil(
            tech["count"] * (1 - progress) * ingredient["amount"]) - math.floor(stock)))
    try:
        ledger = SupplyLedger.capture(snapshot, catalog, reserved=reserved, job=job)
        bill = catalog.material_demands(roots, ledger.forecast_stock(), snapshot.researched or [])
        demand = {}
        # Expansion credits collectible source output, which still needs hauling.
        # Reconstructed recipe input bills must distinguish it from spendable
        # carried stock. Allocate the latter once, in stable expansion order;
        # reserved/locked items were already excluded by the shared ledger.
        carried = dict(ledger.carried)
        for name, batches in bill.batches.items():
            if name not in RECIPES:
                continue
            recipe = catalog.recipes[name]
            if any(i.get("type") != "item" for i in recipe.get("ingredients", [])):
                continue
            demand[name] = {}
            for ingredient in recipe["ingredients"]:
                item = ingredient["name"]
                wanted = math.ceil(ingredient["amount"] * batches)
                used = min(wanted, math.floor(carried.get(item, 0)))
                carried[item] = carried.get(item, 0) - used
                demand[name][item] = wanted - used
        return demand, "current_research_recipe_bill"
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return {}, "bounded_demand_unavailable"


def service_cost(row, outcomes, now_tick: int) -> tuple[int, str, dict]:
    """Distinct identity-bound decision-to-receipt ticks, never CPU/network time."""
    samples = {"factory_extract": [], "factory_insert": []}
    seen_ids, seen_receipts = set(), set()
    last_finish = -1
    for outcome in (outcomes[-MAX_HISTORY:] if isinstance(outcomes, (list, tuple)) else []):
        try:
            validate_attempt(outcome, finished=True)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
        action = outcome["action"]
        if action not in samples or outcome["id"] in seen_ids or not outcome["receipt"]:
            continue
        if outcome["receipt"] in seen_receipts:
            continue
        endpoint = row["source"] if action == "factory_extract" else row["target"]
        start, finish = outcome["started_tick"], outcome["finished_tick"]
        suffix = f":{action}:{endpoint['role']}:{row['item']}"
        receipt = outcome["receipt"]
        prefix = receipt[:-len(suffix)] if receipt.endswith(suffix) else ""
        if (not prefix.isascii() or not prefix.isdigit() or len(prefix) > 16
                or not 0 <= int(prefix) <= start or start < last_finish):
            continue
        if (outcome["origin"] != "new" or outcome["outcome"] != "verified"
                or outcome["expected_unit_number"] != endpoint["unit_number"]
                or outcome["plan_id"] != f"factory:{action}:{endpoint['role']}"
                or not 0 <= now_tick - finish <= MAX_SERVICE_TICKS
                or not 0 < finish - start <= MAX_SERVICE_TICKS):
            continue
        seen_ids.add(outcome["id"]); seen_receipts.add(outcome["receipt"])
        last_finish = finish
        samples[action].append(finish - start)
    counts = {action: len(values) for action, values in samples.items()}
    if min(counts.values()) >= 3:
        return math.ceil(sum(median(v) for v in samples.values())), "verified_attempt_game_ticks", counts
    distance = sum(abs(row["source"]["position"][axis] - row["target"]["position"][axis])
                   for axis in ("x", "y"))
    estimate = 2 * distance * TRAVEL_TICKS_PER_TILE + 2 * SERVICE_TICKS
    return min(MAX_SERVICE_TICKS, math.ceil(estimate)), "estimated_round_trip_and_service", counts


def _owned_supply(row, snapshot) -> int:
    """Only current source output; unproduced supply never finances the investment."""
    source = snapshot.factory["entities"][row["source"]["role"]]
    stock = source.get("output", {})
    # The native factory reports chest contents in the same output field.
    if not isinstance(stock, dict) or len(stock) > 200 or any(not finite(v) for v in stock.values()):
        raise ValueError("Invalid source inventory")
    if any(value > 0 and item != row["item"] for item, value in stock.items()):
        raise ValueError("Mixed source inventory")
    return math.floor(stock.get(row["item"], 0))


def offer_value(row, snapshot, catalog, demand, outcomes=()) -> dict:
    """Conservative declared estimates. No stock/rate forecast proves native flow."""
    try:
        if row["target"]["inventory"] != "input":
            return {"eligible": False, "reason": "coal_policy_required"}
        source = snapshot.factory["entities"][row["source"]["role"]]
        target = snapshot.factory["entities"][row["target"]["role"]]
        if not contract.current(row, snapshot):
            return {"eligible": False, "reason": "stale_route"}
        if (not finite(target.get("energy")) or target["energy"] <= 0
                or row["source"]["inventory"] == "output"
                and (not finite(source.get("energy")) or source["energy"] <= 0)):
            return {"eligible": False, "reason": "missing_power"}
        stock = target.get("input", {}).get(row["item"], 0)
        wanted = demand.get(target.get("recipe", ""), {}).get(row["item"], 0)
        if not finite(stock) or not finite(wanted):
            return {"eligible": False, "reason": "invalid_destination_stock"}
        missing = max(0, wanted - math.floor(stock))
        if not missing:
            return {"eligible": False, "reason": "no_current_recipe_deficit"}
        available = _owned_supply(row, snapshot)
        units = min(missing, available)
        if not units:
            return {"eligible": False, "reason": "source_depleted"}
        stack = catalog.stack_sizes.get(row["item"], 20)
        if not contract.integer(stack, 1, 10_000):
            return {"eligible": False, "reason": "unknown_handling_capacity"}
        batch = min(20, stack)
        kit = contract.remaining(row)
        # Value the consumed kit even when already carried. This opportunity-cost
        # estimate uses catalog recipe processing, not a claim about acquisition.
        material_bill = catalog.material_demands(kit, {}, snapshot.researched or [])
        craft = 0.0
        for recipe_name, batches in material_bill.batches.items():
            energy = catalog.recipes[recipe_name].get("energy")
            if not finite(energy) or energy <= 0:
                raise ValueError("Unknown kit process cost")
            craft += energy * batches * 60
        if any(item not in {"iron-ore", "copper-ore", "coal", "stone", "wood"}
               for item in material_bill.shortages):
            raise ValueError("Unavailable kit recipe")
        trip, basis, counts = service_cost(row, outcomes, snapshot.tick)
        trips = math.ceil(units / batch)
        components = sum(s["part"] not in row["parts"] for s in row["steps"])
        build = math.ceil(craft + 2 * SERVICE_TICKS * (components + 1))
        manual = trips * trip
        eligible = manual > build * 1.25
        return {"eligible": eligible, "reason": "bounded_payback" if eligible else "manual_service_cheaper",
                "demand_units": math.ceil(missing), "source_units": available, "valued_units": math.ceil(units),
                "horizon_packs_cap": HORIZON_PACKS, "manual_trips_estimate": trips,
                "service_game_ticks": trip, "service_basis": basis, "service_samples": counts,
                "build_game_ticks_estimate": build, "manual_game_ticks_estimate": manual,
                "cost_margin": 1.25, "remaining_kit": kit,
                "kit_cost_basis": "catalog_energy_plus_estimated_construction_service",
                "supply_basis": "currently_owned_source_output_only", "flow_proven": False}
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return {"eligible": False, "reason": "cost_or_supply_unavailable"}


def candidates(snapshot, catalog, goal, *, outcomes=(), reserved=None, job=None,
               capital_active=False) -> tuple[list[Plan], dict]:
    """At most two offers and one paid construction project, retaining manual work."""
    rows = contract.routes(snapshot)
    diagnostics = {"reason": "current_research_recipe_bill", "routes": {}}
    if (goal != "rocket_launch" or job is not None or snapshot.factory.get("crafting_queue", 0)
            or capital_active or any(row["pending"] for row in rows.values())):
        return [], {"reason": "goal_or_pending_or_other_investment", "routes": {}}
    builders = {p.steps[0].parameters["route"]: p for p in build_candidates(snapshot, goal)}
    active = sorted(key for key, row in rows.items() if row["state"] == "building")
    demand, diagnostics["reason"] = requirements(snapshot, catalog, reserved=reserved, job=job)
    offers = []
    for key, row in sorted(rows.items()):
        if not contract.current(row, snapshot) or row["state"] not in {"proposed", "building"}:
            continue
        if active and key != active[0]:
            diagnostics["routes"][key] = {"eligible": False, "reason": "existing_paid_project"}
            continue
        value = ({"eligible": True, "reason": "preserve_paid_commitment", "remaining_kit": contract.remaining(row)}
                 if row["state"] == "building" else offer_value(row, snapshot, catalog, demand, outcomes))
        diagnostics["routes"][key] = value
        if not value["eligible"]:
            continue
        try:
            ledger = SupplyLedger.capture(snapshot, catalog, reserved=reserved, job=job)
            # The controller excludes this project's own lock at final dispatch;
            # new offers must be wholly fundable before the first paid placement.
            if row["state"] == "proposed" and any(ledger.carried.get(k, 0) < v for k, v in contract.remaining(row).items()):
                diagnostics["routes"][key] = {**value, "eligible": False, "reason": "insufficient_unreserved_kit"}
                continue
        except (ValueError, KeyError, TypeError):
            diagnostics["routes"][key] = {"eligible": False, "reason": "reservation_or_supply_invalid"}
            continue
        plan = builders.get(key)
        if plan is None:
            diagnostics["routes"][key] = {**value, "eligible": False, "reason": "fresh_construction_precondition"}
            continue
        marker = {"schema": 1, "route": key, "layout": row["layout"], "observed_tick": snapshot.tick,
                  "research": snapshot.factory.get("research", ""), **value}
        offers.append(replace(plan, materials={**(plan.materials or {}), MARKER: marker}))
        if len(offers) == MAX_OFFERS:
            break
    return offers, diagnostics


def fresh_permission(plan, step, snapshot, catalog, *, outcomes=(), reserved=None, job=None) -> bool:
    """Recompute policy at the fresh precondition; never trust a ranking annotation."""
    marker = (plan.materials or {}).get(MARKER)
    if not isinstance(marker, dict) or step.action != contract.COMMAND or len(plan.steps) != 1:
        return False
    try:
        row = contract.routes(snapshot).get(marker.get("route"))
        if (not row or not contract.current(row, snapshot) or row["layout"] != marker.get("layout")
                or marker.get("schema") != 1 or type(marker.get("schema")) is not int
                or not contract.integer(marker.get("observed_tick")) or marker["observed_tick"] > snapshot.tick
                or not contract.allowed(step.parameters, snapshot)):
            return False
        expected = next((p for p in build_candidates(snapshot, plan.goal)
                         if p.steps[0].parameters["route"] == row["route"]), None)
        if expected is None or expected.id != plan.id:
            return False
        # The durable original receipt intentionally survives advancing ticks.
        p, e = dict(step.parameters), dict(expected.steps[0].parameters)
        p.pop("receipt"); e.pop("receipt")
        if p != e or step.costs != expected.steps[0].costs or step.effect != "solid_component":
            return False
        if row["state"] != "proposed":
            return True  # An exact prepared/paid project retains its ownership.
        if job is not None or snapshot.factory.get("crafting_queue", 0):
            return False
        demand, _ = requirements(snapshot, catalog, reserved=reserved, job=job)
        return offer_value(row, snapshot, catalog, demand, outcomes).get("eligible") is True
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def ranking_marker(plan, snapshot) -> bool:
    """Only exact annotations generated in this decision can buy optional priority."""
    marker = (plan.materials or {}).get(MARKER)
    approved = getattr(snapshot, "_solid_investment_annotations", {})
    return (isinstance(marker, dict) and marker == approved.get(plan.id)
            and marker.get("observed_tick") == snapshot.tick and marker.get("eligible") is True)
