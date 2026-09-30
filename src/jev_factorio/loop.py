"""The agent loop: observe -> describe options -> Jev decides -> act.

Design notes
------------
* Cadence: macro decisions every `tick_seconds` (default 2s). Jev's 70-500 ms
  latency fits comfortably inside that budget; per-tick (60 UPS) control is
  neither affordable nor needed - Factorio is a planning game.
* Confidence gate: Choice answers carry `confidence` (0-1, from the answer
  distribution). Below the floor we run the scripted fallback instead,
  because Jev always picks a winner even when no option fits.
  See https://docs.typesafe.ai/patterns/confidence-routing.md
* One call per tick fans out goal/action/stuck/urgency questions together.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import requests

from .causal_trace import CausalTrace, traced_step
from .iteration_timing import loop_sleep
from .research_log import EventSink, validate_output_paths
from .jev_client import make_client
from .questions import build_questions
from .state import GameSnapshot
from .provenance import gameplay_context


class _DefaultSteps:
    """Distinguish an omitted step limit from an explicit ``None``."""


_DEFAULT_STEPS = _DefaultSteps()


def fallback_policy(snapshot: GameSnapshot) -> str:
    """Deterministic policy for low-confidence ticks (and the mock demo)."""
    inventory = snapshot.inventory
    nearby = snapshot.nearby_resources
    has_drill = any(entity.startswith("burner-mining-drill")
                    for entity in snapshot.placed_entities)
    if has_drill and (snapshot.drill_fuel > 0 or snapshot.drill_status == "working"):
        return "idle"
    if has_drill and inventory.get("coal", 0) > 0:
        return "fuel_drill"
    if has_drill or inventory.get("burner-mining-drill", 0) > 0:
        if inventory.get("coal", 0) < 5:
            if "coal" not in nearby:
                return "idle"
            return "walk_to_coal" if nearby["coal"] > 0.5 else "mine_coal"
        if "iron-ore" in nearby:
            return "walk_to_iron" if nearby["iron-ore"] > 0.5 else "place_burner_drill"
    return "idle"


class AgentLoop:
    def __init__(self, backend, jev=None, confidence_floor: float = 0.45,
                 tick_seconds: float = 2.0, log_file: str | None = None, *,
                 research_log: EventSink | None = None):
        validate_output_paths(research_log, log_file)
        self.provenance = gameplay_context()
        self.backend = backend
        self.jev = jev or make_client()
        self._trace = CausalTrace(research_log, "flat", self.jev, provenance=self.provenance)
        self._decision_client = self._trace.client(self.jev)
        self.confidence_floor = confidence_floor
        self.tick_seconds = tick_seconds
        self.log_file = Path(log_file) if log_file else None
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)

    @traced_step
    def step(self) -> dict:
        snapshot = self._trace.observe(self.backend, "before_decision")
        questions = self._trace.call(
            "candidate_set_created", lambda: build_questions(snapshot),
            result=lambda batch: {"candidates": batch["next_action"]["criteria"], "questions": batch})
        answers = self._decision_client.evaluate(snapshot.for_jev(), questions)

        action_ans = answers["next_action"]
        candidates = questions["next_action"]["criteria"]
        if action_ans["confidence"] >= self.confidence_floor and action_ans["choice"] in candidates:
            action = action_ans["choice"]
            source = "jev"
        else:
            action = fallback_policy(snapshot)
            source = "fallback"
        if action not in candidates:
            action = "idle"

        self._trace.emit("decision", {"action": action, "source": source,
                                      "requested_action": action_ans.get("choice"),
                                      "confidence": action_ans["confidence"],
                                      "confidence_floor": self.confidence_floor,
                                      "model_called": True})
        outcome = self._trace.dispatch(lambda: self.backend.act(action), action, role="flat")
        after = self._trace.observe(self.backend, "after_action")
        self._trace.emit("verification", {"phase": "flat", "verified": None,
                                          "reason": "flat_controller_has_no_postcondition_predicate"})
        record = {
            **self.provenance,
            "tick": snapshot.tick, "goal": answers["goal"]["choice"],
            "action": action, "source": source,
            "confidence": action_ans["confidence"],
            "stuck_p": answers["is_stuck"]["noul"],
            "urgency": answers["urgency"]["score"], "outcome": outcome,
            "state": snapshot.for_jev(), "questions": questions,
            "answers": answers, "after_state": after.for_jev(),
            "usage": getattr(self.jev, "last_usage", None),
        }
        if self.log_file:
            with self.log_file.open("a") as output:
                output.write(json.dumps(record) + "\n")
        print(f"[t={record['tick']:>4}] {source:>8} -> {action:<20} "
              f"(conf {record['confidence']:.2f})  {outcome}", flush=True)
        return record

    def run(self, steps: int | None | _DefaultSteps = _DEFAULT_STEPS,
            duration_seconds: float | None = None, *, until_complete: bool = False,
            after_step: Callable[[AgentLoop, int, dict], bool] | None = None) -> None:
        if type(until_complete) is not bool:
            raise ValueError("until_complete must be a boolean")
        if steps is _DEFAULT_STEPS:
            steps = None if until_complete else 10
        if until_complete:
            if steps is not None or duration_seconds is not None:
                raise ValueError("until_complete cannot be combined with a step or duration limit")
            if type(getattr(self, "terminal", None)) is not bool:
                raise ValueError("until_complete requires a controller with terminal status")
            if after_step is not None:
                raise ValueError("until_complete cannot use an owner step gate")
        if steps is None and duration_seconds is None and not until_complete:
            raise ValueError("A step or duration limit is required")
        deadline = time.monotonic() + duration_seconds if duration_seconds is not None else None
        completed = 0
        while until_complete or steps is None or completed < steps:
            if getattr(self, "terminal", False):
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            from .planning.scheduling import poll_delay
            delay = self.tick_seconds
            step_succeeded = False
            try:
                record = self.step()
                completed += 1
                step_succeeded = True
                delay = poll_delay(self)
            except requests.RequestException as error:
                status = error.response.status_code if error.response is not None else None
                if deadline is None or (
                    status is not None and status != 429 and status < 500
                ):
                    raise
                print(f"Transient API failure ({status or type(error).__name__}); retrying.",
                      flush=True)
                delay = max(30, delay)
            if until_complete and step_succeeded and getattr(self, "terminal", False):
                break
            if (step_succeeded and after_step is not None
                    and (steps is None or completed < steps)
                    and not getattr(self, "terminal", False)):
                if after_step(self, completed, record) is not True:
                    break
            if deadline is not None:
                delay = min(delay, max(0, deadline - time.monotonic()))
            loop_sleep(self, delay, lambda: time.sleep(delay))
