"""One legal next component per owned corridor, never a forecast authorization."""
from __future__ import annotations

import hashlib
import json

from .. import solid_routes as routes
from ..skills import Plan, Step


def next_component(row: dict, snapshot, goal: str, *, receipt: str) -> Plan | None:
    """Describe the exact next component; this does not establish journal ownership.

    ``row`` comes from the validated native route map in the supplied snapshot.
    Fresh candidate admission supplies a new receipt only without native pending
    work. The investment validator may pass the retained receipt when checking
    an existing plan. Only the controller's durable-owner and replay guards can
    authorize recovery; this helper cannot adopt a journal or start an attempt.
    """
    key = row["route"]
    if not routes.current(row, snapshot):
        return None
    todo = [s for s in row["steps"] if s["part"] not in row["parts"]]
    if not todo:
        return None  # Full/backpressured/no-power routes never rebuild.
    step = todo[0]
    parameters = {"route": key, "layout": row["layout"], "part": step["part"],
                  "receipt": receipt}
    # Project failure identity binds the immutable intent, not replaceable
    # unit IDs, geometry, quantity or tick. Action preconditions still bind
    # the exact observed physical endpoints/layout/receipt.
    binding = [row["source"]["role"], row["target"]["role"], row["item"], row["target"]["inventory"]]
    project = hashlib.sha256(json.dumps(binding, separators=(",", ":"), ensure_ascii=True).encode("ascii")).hexdigest()
    if not isinstance(receipt, str) or len(receipt) > 128 or not routes.allowed(parameters, snapshot):
        return None
    bill = routes.remaining(row)
    return Plan(f"solid-project:{project}:{step['part']}", goal,
                f"Build paid {step['part']} transporting {row['item']} to {row['target']['role']}",
                (Step(routes.COMMAND, "solid_component", costs={step["name"]: 1},
                      parameters=parameters, timeout_ticks=1800),),
                materials={"solid_route": {"route": key, "next_component": step["part"],
                    "remaining_kit": bill, "evidence": "observed_geometry_not_flow",
                    "reason": "explicit_foundation_intent", "investment_payback": "not_evaluated"}})


def candidates(snapshot, goal: str, *, limit: int = 4) -> list[Plan]:
    if type(limit) is not int or not 1 <= limit <= routes.MAX_ROUTES:
        raise ValueError("Invalid solid candidate bound")
    rows = routes.routes(snapshot)
    # Recovery only runs from an already durable pending operation, never by
    # copying a native journal's receipt into a freshly selected plan.
    if any(row["pending"] for row in rows.values()):
        return []
    plans = []
    for key, row in sorted(rows.items()):
        todo = [s for s in row["steps"] if s["part"] not in row["parts"]]
        if not todo:
            continue
        plan = next_component(row, snapshot, goal,
                              receipt=f"{snapshot.tick}:{key}:{todo[0]['part']}")
        if plan is not None:
            plans.append(plan)
        if len(plans) == limit:
            break
    return plans
