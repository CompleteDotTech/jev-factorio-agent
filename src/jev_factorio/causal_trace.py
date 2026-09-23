"""Passive causal instrumentation at existing controller call boundaries."""
from __future__ import annotations

import time
from dataclasses import asdict
from copy import deepcopy
from functools import wraps
from typing import Callable, TypeVar
from uuid import uuid4

import requests

from .research_log import EventSink, ResearchLogError, safe_payload

T = TypeVar("T")


def error_facts(error: BaseException) -> dict:
    """Fixed vocabulary only: no exception text, request URL, headers, or repr."""
    status = None
    if isinstance(error, requests.Timeout):
        kind = "timeout"
    elif isinstance(error, requests.ConnectionError):
        kind = "connection"
    elif isinstance(error, requests.HTTPError):
        kind = "http"
        if error.response is not None:
            value = error.response.status_code
            status = value if type(value) is int and 100 <= value <= 599 else None
    elif isinstance(error, (KeyboardInterrupt, SystemExit)):
        kind = "interrupted"
    elif isinstance(error, (ValueError, TypeError)):
        kind = "invalid_data"
    else:
        kind = "other"
    return {"category": kind, "http_status": status}


def traced_step(method):
    """Keep uncaught failures visible without altering return values/retries."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        trace = getattr(self, "_trace", None)
        if trace is None or not trace.enabled:
            return method(self, *args, **kwargs)
        trace.begin_step()
        try:
            result = method(self, *args, **kwargs)
        except BaseException as error:
            trace.error("step_failed", error)
            raise
        trace.emit("step_finished", {key: result.get(key) for key in
                                    ("action", "source", "status", "verified")})
        return result
    return wrapper


class CausalTrace:
    def __init__(self, sink: EventSink | None, controller: str, client=None, *, provenance=None):
        self.metrics = None
        self.sink, self.controller = sink, controller
        self.enabled = sink is not None
        self.trace_id = uuid4().hex if self.enabled else None
        self.observation_phase = "before_decision"
        self._counts: dict[str, int] = {}
        self._failed = False
        self.decision_id = self.observation_id = self.model_call_id = self.action_id = None
        self._session_id = self._world_kind = self._tick = None
        self._pending_key = self._pending_action_id = None
        self._attempt_actions: dict[str, str] = {}
        self.provenance = deepcopy(provenance or {})
        self._secrets = tuple(value for name in ("api_key", "api_token")
                              if isinstance(value := getattr(client, name, None), str) and value) \
            if self.enabled else ()

    def identity(self, kind: str) -> str | None:
        if not self.enabled:
            return None
        self._counts[kind] = self._counts.get(kind, 0) + 1
        return f"{kind}:{self._counts[kind]}"

    def begin_step(self) -> None:
        if self._failed:
            raise ResearchLogError("Causal trace has failed")
        self.decision_id = self.identity("decision")
        self.observation_id = self.model_call_id = self.action_id = None
        self._tick = None
        self.emit("step_started", {})

    def emit(self, event_type: str, payload: dict) -> None:
        if not self.enabled:
            return
        if self._failed:
            raise ResearchLogError("Causal trace has failed")
        try:
            envelope = {"trace_id": self.trace_id, "controller": self.controller,
                        "decision_id": self.decision_id,
                        "observation_id": self.observation_id,
                        "model_call_id": self.model_call_id, "action_id": self.action_id,
                        "session_id": self._session_id, "world_kind": self._world_kind,
                        "factorio_tick": self._tick, "supervisor_provenance": self.provenance,
                        **payload}
            # Even a custom sink must not retain or mutate live controller data.
            self.sink.emit(event_type, safe_payload(envelope, self._secrets))
        except Exception:
            self._failed = True
            raise ResearchLogError("Causal event persistence failed") from None

    def error(self, event_type: str, error: BaseException, **details) -> None:
        # Preserve the primary exception if the attempt to log it also fails.
        if not self.enabled or self._failed:
            return
        try:
            self.emit(event_type, {"status": "error", "error": error_facts(error), **details})
        except ResearchLogError:
            pass

    def call(self, event_type: str, operation: Callable[[], T], *,
             details: dict | None = None,
             result: Callable[[T], dict] | None = None) -> T:
        if not self.enabled and self.metrics is None:
            return operation()
        if self._failed:
            raise ResearchLogError("Causal trace has failed")
        start = time.perf_counter_ns()
        try:
            value = operation()
        except BaseException as error:
            elapsed = time.perf_counter_ns() - start
            if self.metrics is not None:
                self.metrics.call(event_type, elapsed, failed=True)
            self.error(event_type, error, duration_ns=elapsed, **(details or {}))
            raise
        elapsed = time.perf_counter_ns() - start
        if self.metrics is not None:
            self.metrics.call(event_type, elapsed)
        if not self.enabled:
            return value
        try:
            captured = result(value) if result else {}
        except Exception:
            self._failed = True
            raise ResearchLogError("Cannot capture a causal event") from None
        self.emit(event_type, {**(details or {}), "status": "ok", "duration_ns": elapsed, **captured})
        return value

    def observe(self, backend, phase: str):
        if not self.enabled:
            return self.call("observation", backend.observe)
        observation_id = self.identity("observation")

        def captured(snapshot):
            self.observation_id = observation_id
            self._session_id, self._world_kind = snapshot.session_id, snapshot.world_kind
            self._tick = snapshot.tick
            return {"observation_id": observation_id, "snapshot": asdict(snapshot),
                    "validation": "not_yet_validated"}

        return self.call("observation", backend.observe,
                         details={"phase": phase, "observation_id": observation_id}, result=captured)

    def client(self, client):
        return TracedClient(client, self) if (self.enabled or self.metrics is not None) and client is not None else client

    def pending_ref(self, plan_id: str, index: int, pending: dict, *, attempt_id=None) -> dict:
        key = (plan_id, index, pending["started_tick"], pending["action"])
        known = key == self._pending_key
        return {"action_id": self._pending_action_id if known else None,
                "action_origin": "current_trace" if known else "checkpoint_or_external",
                "attempt_id": attempt_id,
                "plan_id": plan_id, "step_index": index,
                "started_tick": pending["started_tick"]}

    def clear_pending(self) -> None:
        self._pending_key = self._pending_action_id = None

    def dispatch(self, operation: Callable[[], T], action: str, *, parameters=None,
                 plan_id=None, step_index=None, pending=None, checkpointed=False,
                 role="plan", attempt_id=None) -> T:
        if not self.enabled:
            return operation()
        action_id = self.identity("action")
        self.action_id = action_id
        related = self._pending_action_id if role == "mock_clock_advance" else None
        facts = {"action_id": action_id, "action": action, "parameters": parameters or {},
                 "plan_id": plan_id, "step_index": step_index, "role": role,
                 "attempt_id": attempt_id,
                 "related_action_id": related}
        self.emit("action_prepared", {**facts, "checkpointed": checkpointed,
                                      "pending": pending, "dispatch": "prepared"})
        if pending is not None and role == "plan":
            self._pending_key = (plan_id, step_index, pending["started_tick"], action)
            self._pending_action_id = action_id
            if attempt_id is not None:
                self._attempt_actions[attempt_id] = action_id
        return self.call("action_returned", operation, details=facts,
                         result=lambda outcome: {"outcome": outcome})

    def verify(self, step, snapshot, *, plan_id: str, index: int,
               pending: dict, phase: str, attempt_id=None) -> bool:
        if not self.enabled:
            return step.satisfied(snapshot)
        return self.call("verification", lambda: step.satisfied(snapshot),
                         details={**self.pending_ref(plan_id, index, pending, attempt_id=attempt_id),
                                  "phase": phase, "predicate": asdict(step)},
                         result=lambda satisfied: {"verified": satisfied})

    def attempt_ref(self, attempt_id) -> dict:
        action_id = self._attempt_actions.get(attempt_id)
        return {"attempt_id": attempt_id, "action_id": action_id,
                "action_origin": "current_trace" if action_id else "checkpoint_or_external"}

    def release_attempt(self, attempt_id) -> None:
        self._attempt_actions.pop(attempt_id, None)


class TracedClient:
    """Delegate metadata unchanged; record the exact evaluate boundary once."""
    def __init__(self, client, trace: CausalTrace):
        self._client, self._trace = client, trace

    def __getattr__(self, name):
        return getattr(self._client, name)

    def evaluate(self, state: dict, questions: dict) -> dict:
        trace, client = self._trace, self._client
        trace.model_call_id = trace.identity("model")
        trace.emit("model_request", {"state": state, "questions": questions,
                                     "requested_model": getattr(client, "model", None),
                                     "is_mock": getattr(client, "is_mock", False),
                                     "dispatch": "prepared"})
        return trace.call("model_response", lambda: client.evaluate(state, questions),
                          details={"requested_model": getattr(client, "model", None),
                                   "resolved_model": None, "usage": None},
                          result=lambda answers: {"answers": answers,
                                                  "resolved_model": getattr(client, "last_model", None),
                                                  "usage": getattr(client, "last_usage", None)})
