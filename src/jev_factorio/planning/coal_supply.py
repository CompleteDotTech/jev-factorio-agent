"""Bounded coal construction alternatives; ordinary science and fuel work coexist."""
from __future__ import annotations

import hashlib
from copy import deepcopy

from .. import coal_supply as coal, solid_routes as solid
from ..skills import Plan, Step
from .solid_routes import candidates as corridor_candidates

MARKER = "coal_network"


def source_project(target: str, part: str) -> str:
    # Failure history does not change with a new layout, receipt, tick or entity.
    return f"coal-project:{hashlib.sha256(target.encode('ascii')).hexdigest()}:{part}"


def candidates(snapshot, goal: str, *, failures: dict | None = None) -> list[Plan]:
    rows = coal.sources(snapshot)
    if (not rows or not all(coal.current(row, snapshot) for row in rows.values())
            or any(row["pending"] for row in rows.values())
            or any(row["pending"] for row in solid.routes(snapshot).values())):
        return []  # Native journals belong only to the retained recovery path.
    bill = coal.remaining_kit(rows, snapshot)
    if not all(snapshot.inventory.get(item, 0) >= n for item, n in bill.items()):
        return []
    failures = failures or {}
    corridors = {p.steps[0].parameters["route"]: p for p in corridor_candidates(snapshot, goal)}
    proposals = []
    bundle = {target: coal.commitment(row) for target, row in rows.items()}
    for target, row in sorted(rows.items()):
        todo = [s for s in row["steps"] if s["part"] not in row["parts"]]
        if not todo:
            continue  # A paid source is not rebuilt for backpressure, power loss or depletion.
        spec = todo[0]
        route = coal.route_for(row, snapshot)
        progress = len(row["parts"]) + (len(route["parts"]) if route else 0)
        if spec["part"] == "drill" and route and len(route["parts"]) < len(route["steps"]):
            plan = corridors.get(route["route"])
        else:
            parameters = {"target": target, "layout": row["layout"], "part": spec["part"],
                "receipt": f"{snapshot.tick}:{target}:{spec['part']}"}
            plan = (Plan(source_project(target, spec["part"]), goal,
                f"Build paid coal {spec['part']} for {target}",
                (Step(coal.COMMAND, "coal_component", costs={spec["name"]: 1},
                      parameters=parameters, timeout_ticks=1800),)) if coal.allowed(parameters, snapshot) else None)
        if plan and failures.get(plan.id, 0) < 2:
            materials = deepcopy(plan.materials or {})
            materials[MARKER] = {"bundle": bundle, "remaining_kit": bill,
                                 "target": target, "evidence": "qualified_geometry_not_native_flow"}
            plan = Plan(plan.id, plan.goal, plan.description, plan.steps, materials=materials)
            proposals.append((progress, target, plan))
    if not proposals:
        return []
    # One paid increment per least-advanced eligible branch. Completed/exhausted
    # branches cannot monopolize selection or erase another branch's progress.
    least = min(progress for progress, _, _ in proposals)
    return [plan for progress, _, plan in proposals if progress == least]
