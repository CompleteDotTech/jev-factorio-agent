"""Progress and verified replenishment history; no command execution or retries."""
from __future__ import annotations

import math
import time
from collections import Counter, deque
from copy import deepcopy

SCIENCE = {"automation-science-pack", "logistic-science-pack", "chemical-science-pack",
           "military-science-pack", "production-science-pack", "utility-science-pack", "space-science-pack"}


def counts(value):
    if not isinstance(value, dict):
        return None
    if any(not isinstance(k, str) or type(v) not in {int, float}
           or not math.isfinite(v) or v < 0 for k, v in value.items()):
        return None
    return dict(value)


class ReplenishmentHistory:
    """Demand-to-verified-lab-delivery samples, scoped to a single process/session."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.session = None
        self.pending = {}
        self.samples = {}
        self.deliveries = Counter()
        self._seen = deque(maxlen=4096)
        self.research = None

    def observe(self, snapshot, catalog):
        from .planning.scheduling import research_schedule
        if self.session != snapshot.session_id:
            self.session = snapshot.session_id
            self.pending.clear(); self.samples.clear(); self.deliveries.clear(); self._seen.clear()
        research = snapshot.factory.get("research", "")
        if self.research != research:
            self.pending.clear()
            self.research = research
        rows = research_schedule(snapshot, catalog)
        needed = {row["item"] for row in rows if row["due"]}
        self.pending = {item: start for item, start in self.pending.items()
                        if 0 <= snapshot.tick - start[0] <= 216000}
        for item in needed:
            self.pending.setdefault(item, (snapshot.tick, self.clock()))
        return self.evidence(snapshot.tick)

    def delivered(self, step, snapshot, attempt_id):
        parameters = step.parameters or {}
        if (step.action != "factory_insert" or parameters.get("role") != "utility:lab"
                or snapshot.session_id != self.session or attempt_id in self._seen
                or not attempt_id or not step.satisfied(snapshot)):
            return
        item, amount = parameters.get("item"), parameters.get("quantity")
        if not isinstance(item, str) or type(amount) is not int or amount <= 0:
            return
        self._seen.append(attempt_id)
        self.deliveries[item] += amount
        start = self.pending.pop(item, None)
        if start is None:
            return
        ticks, seconds = snapshot.tick - start[0], self.clock() - start[1]
        if ticks <= 0 or not math.isfinite(seconds) or seconds <= 0:
            return
        rows = self.samples.setdefault(item, deque(maxlen=16))
        rows.append({"lead_ticks": min(216000, ticks), "wall_seconds": seconds,
                     "observed_tick": snapshot.tick, "delivered": amount})

    def evidence(self, tick):
        return {item: {"schema": 1, "session_id": self.session, "samples": len(rows),
                       "lead_ticks": max(row["lead_ticks"] for row in rows),
                       "observed_tick": rows[-1]["observed_tick"],
                       "max_wall_seconds": max(row["wall_seconds"] for row in rows),
                       "basis": "demand_to_verified_lab_receipt"}
                for item, rows in self.samples.items() if rows and 0 <= tick - rows[-1]["observed_tick"] <= 216000}


class ProgressMonitor:
    def __init__(self, window_seconds=1800, clock=time.monotonic):
        if type(window_seconds) not in {int, float} or not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("Progress window must be finite and positive")
        self.window = window_seconds
        self.clock = clock
        self.history = deque(maxlen=4096)
        self.session = None
        self.decision = 0
        self.last_report = {}

    def update(self, snapshot, catalog, action, status, phases=(), delivered=None):
        now = self.clock()
        produced = counts(snapshot.factory.get("produced"))
        consumed = counts(snapshot.factory.get("consumed"))
        research = snapshot.factory.get("research", "")
        progress = snapshot.factory.get("research_progress")
        if type(progress) not in {int, float} or not math.isfinite(progress) or not 0 <= progress <= 1:
            progress = None
        reset = self.session != snapshot.session_id
        if self.history:
            previous = self.history[-1]
            reset |= snapshot.tick < previous["tick"] or now < previous["time"]
            for key, current in (("produced", produced), ("consumed", consumed)):
                old = previous[key]
                if current is not None and old is not None:
                    reset |= any(current.get(k, 0) < v for k, v in old.items())
        if reset:
            self.history.clear()
            self.session = snapshot.session_id
            self.decision = 0
        self.decision += 1
        current = {"time": now, "tick": snapshot.tick, "decision": self.decision,
                   "produced": produced, "consumed": consumed, "research": research,
                   "progress": progress, "researched": set(snapshot.researched or []),
                   "delivered": dict(delivered or {})}
        self.history.append(current)
        while len(self.history) > 2 and self.history[1]["time"] <= now - self.window:
            self.history.popleft()
        baseline = self.history[0]
        elapsed = now - baseline["time"]
        observed_decisions = self.decision - baseline["decision"]
        def delta(key):
            left, right = baseline[key], current[key]
            if left is None or right is None or any(row[key] is None for row in self.history):
                return None
            return {k: right.get(k, 0) - left.get(k, 0) for k in left.keys() | right.keys()
                    if right.get(k, 0) != left.get(k, 0)}
        material, consumption, delivery = delta("produced"), delta("consumed"), delta("delivered")
        technology_progress = bool(current["researched"] - baseline["researched"])
        research_progress = technology_progress or (
            research == baseline["research"] and progress is not None
            and baseline["progress"] is not None and progress > baseline["progress"])
        science_consumed = sum(v for k, v in (consumption or {}).items() if k in SCIENCE)
        science_delivered = sum(v for k, v in (delivery or {}).items() if k in SCIENCE)
        from .planning.scheduling import research_schedule
        supplies = research_schedule(snapshot, catalog) if catalog else []
        starved = [row["item"] for row in supplies if row["available"] == 0 and row["remaining"] > 0]
        durations = Counter()
        for event in phases:
            if event.get("status") in {"returned", "failed"} and type(event.get("seconds")) in {int, float}:
                if event.get("stage") in {"observe", "pre_dispatch_observe", "post_dispatch_observe"}:
                    durations["observing"] += event["seconds"]
                elif event.get("stage") == "approach":
                    durations["travelling"] += event["seconds"]
                elif event.get("stage") == "dispatch":
                    durations["dispatch"] += event["seconds"]
        activity = "working"
        if status in {"uncertain", "blocked"}:
            activity = "repair-required"
        elif starved:
            activity = "supply-starved"
        elif durations["observing"] > durations["dispatch"]:
            activity = "observing"
        elif durations["travelling"] > 0:
            activity = "travelling"
        elif action in {"observe", "verify", "idle", "factory_wait"}:
            activity = "waiting"
        complete = elapsed >= self.window
        self.last_report = {"schema": 1, "window_seconds": self.window,
            "elapsed_seconds": elapsed, "window_complete": complete,
            "scope": "current_process_session_rolling_window", "activity": activity,
            "starved_science": starved, "research_progress": bool(research_progress),
            "technology_progress": technology_progress, "produced_delta": material,
            "consumed_delta": consumption, "verified_lab_delivery_delta": delivery,
            "science_consumed_per_actor_minute": science_consumed * 60 / elapsed if elapsed and consumption is not None else None,
            "science_delivered_per_actor_minute": science_delivered * 60 / elapsed if elapsed else None,
            "science_consumed_per_decision": science_consumed / observed_decisions if observed_decisions and consumption is not None else None,
            "research_progress_known": progress is not None and baseline["progress"] is not None and research == baseline["research"],
            "no_research_progress_alert": complete and bool(research) and not research_progress
                and progress is not None and baseline["progress"] is not None and research == baseline["research"],
            "no_material_progress_alert": complete and material is not None and not any(v > 0 for v in material.values()),
            "no_science_consumption_alert": complete and bool(research) and consumption is not None and science_consumed <= 0,
            "counter_epoch_reset": bool(reset), "native_victory": snapshot.victory is True and snapshot.world_kind == "fle"
            and snapshot.victory_source == "native:base-game-rocket-launch"}
        return deepcopy(self.last_report)
