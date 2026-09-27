"""One legal next component per owned corridor, never a forecast authorization."""
from __future__ import annotations

import hashlib
import json

from .. import solid_routes as routes
from ..skills import Plan, Step


def candidates(snapshot, goal: str, *, limit: int = 4) -> list[Plan]:
    if type(limit) is not int or not 1 <= limit <= routes.MAX_ROUTES:
        raise ValueError("Invalid solid candidate bound")
    plans = []
    for key, row in sorted(routes.routes(snapshot).items()):
        if not routes.current(row, snapshot):
            continue
        todo = [s for s in row["steps"] if s["part"] not in row["parts"]]
        if not todo:
            continue  # Full/backpressured/no-power routes never rebuild or wait forever.
        step = todo[0]
        parameters = {"route": key, "layout": row["layout"], "part": step["part"],
                      "receipt": f"{snapshot.tick}:{key}:{step['part']}"}
        # Project failure identity binds the immutable intent, not replaceable
        # unit IDs, geometry, quantity or tick. Action preconditions still bind
        # the exact observed physical endpoints/layout/receipt.
        binding = [row["source"]["role"], row["target"]["role"], row["item"], row["target"]["inventory"]]
        project = hashlib.sha256(json.dumps(binding, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
        if len(parameters["receipt"]) > 128 or not routes.allowed(parameters, snapshot):
            continue
        bill = routes.remaining(row)
        plans.append(Plan(f"solid-project:{project}:{step['part']}", goal,
                          f"Build paid {step['part']} transporting {row['item']} to {row['target']['role']}",
                          (Step(routes.COMMAND, "solid_component", costs={step["name"]: 1},
                                parameters=parameters, timeout_ticks=1800),),
                          materials={"solid_route": {"route": key, "next_component": step["part"],
                              "remaining_kit": bill, "evidence": "observed_geometry_not_flow",
                              "reason": "explicit_foundation_intent", "investment_payback": "not_evaluated"}}))
        if len(plans) == limit:
            break
    return plans
