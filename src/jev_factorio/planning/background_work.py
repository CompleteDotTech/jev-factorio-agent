"""Bounded research resupply and independent work during a tracked handcraft."""
from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import replace

from ..craft_jobs import CraftJob
from ..skills import Plan, Step
from .ready_work import ReadyWorkPlanner
from .demand import SupplyLedger


def research_demands(snapshot, catalog, *, early: bool = False) -> list[tuple[str, int]]:
    """Return at most eight pack demands, capped by remaining native research."""
    factory = snapshot.factory
    name = factory.get("research", "")
    tech = catalog.technologies.get(name)
    lab = factory.get("entities", {}).get("utility:lab")
    if not tech or not lab or name in (snapshot.researched or []) or tech.get("trigger"):
        return []
    progress = factory.get("research_progress", 0)
    if type(progress) not in {int, float} or not math.isfinite(progress) or not 0 <= progress <= 1:
        return []
    result = []
    for ingredient in tech.get("ingredients", [])[:8]:
        item, per_unit = ingredient["name"], ingredient["amount"]
        available = lab.get("input", {}).get(item, 0)
        remaining = math.ceil(tech["count"] * (1 - progress) * per_unit)
        target = min(20, remaining)
        if target <= available or (not early and available > min(5, target)):
            continue
        result.append((item, math.ceil(target - available), available / max(1, per_unit)))
    return [(item, amount) for item, amount, _ in sorted(result, key=lambda entry: (entry[2], entry[0]))]


def independent_candidates(goal, snapshot, catalog, job: CraftJob | None = None,
                           planner_type: type[ReadyWorkPlanner] = ReadyWorkPlanner) -> list[Plan]:
    """Use the active production capabilities even during independent work.

    Forecast outputs allow dependency lookahead only. Admission still uses the
    original snapshot and the acknowledged job's output/dispatch locks. Never
    fall back to a less capable planner if a route/buffer plan is unavailable.
    """
    view = deepcopy(snapshot)
    if job:
        view.factory["crafting_queue"] = 0  # Permit planning, never dispatch permission.
        for item, amount in job.outputs.items():
            view.inventory[item] = max(view.inventory.get(item, 0), job.baseline[item] + amount)
    ledger = SupplyLedger.capture(snapshot, catalog, job=job)
    def new_planner():
        worker = planner_type(catalog, view, goal)
        worker.ledger = ledger
        worker.allow_service_visits = False
        return worker
    candidates = []
    worker = new_planner()
    # Keep the existing boiler alive while handcrafting; do not build new power.
    if job and "utility:boiler" in worker.entities:
        try:
            maintenance = worker._fuel("utility:boiler", ())
            if maintenance:
                candidates.append(maintenance)
        except (KeyError, ValueError):
            pass
    probes = 0
    if goal == "rocket_launch":
        for item, amount in research_demands(snapshot, catalog, early=job is not None):
            if job and item in job.outputs:
                continue
            try:
                worker = new_planner()
                if snapshot.inventory.get(item, 0) >= amount:
                    plan = worker._transfer("utility:lab", item, amount)
                else:
                    plan = worker._need(item, amount)
                if plan:
                    candidates.append(replace(plan, description=f"Prefetch research supply: {amount} {item}. " + plan.description))
                # A locked intermediate or busy handcraft must not hide an
                # independent raw ingredient of this same science batch.
                for material, target in sorted(worker.targets.items()):
                    if probes >= 32:
                        break
                    probes += 1
                    probe = new_planner()
                    probe.focus = worker.focus
                    probe.raw_targets = dict(worker.raw_targets)
                    probe.demands = dict(worker.demands)
                    probe.speculative = True
                    try:
                        alternative = probe._need(material, target)
                    except (KeyError, ValueError):
                        continue
                    if alternative:
                        candidates.append(alternative)
            except (KeyError, ValueError):
                continue
    if job:
        try:
            candidates.extend(new_planner().candidates())
        except (KeyError, ValueError):
            pass  # Unsupported lookahead cannot bypass active production rules.
    unique = {}
    for plan in candidates:
        step = plan.steps[0]
        if (len(plan.steps) != 1 or step.action == "factory_wait"
                or (job and not job.permits(step))
                or not step.allowed(snapshot) or step.satisfied(snapshot)):
            continue
        unique.setdefault(plan.id, plan)
        if len(unique) >= 8:
            break
    return list(unique.values())


def background_wait(goal: str, job: CraftJob, tick: int) -> Plan:
    return Plan(
        "background-wait:" + job.parameters["receipt"], goal,
        "No independent ready work; observe the tracked native crafting queue",
        (Step("factory_wait", "crafting_idle", timeout_ticks=max(1, job.deadline_tick - tick)),),
    )
