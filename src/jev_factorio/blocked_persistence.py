"""Explicit, fail-closed polling for two recoverable blocked decisions.

This module never chooses a plan or grants dispatch authority. It records a
content fingerprint before a provider request so an unchanged decision input
cannot be billed again after a restart.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy


RECOVERABLE_REASONS = frozenset({
    "Candidate evidence insufficient", "low choice confidence",
})
MAX_ATTEMPTS = 1024
MAX_WAIT_LEVEL = 9
MAX_WAIT_SECONDS = 300.0
# Both wait paths reach this delay (2 ** 8) before the 300 s cap applies to one of them.
IDLE_DELAY_SECONDS = 256.0
DEFAULT_IDLE_OBSERVATIONS = 6
MAX_IDLE_OBSERVATIONS = 1000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_CLOCK_FACTORY_RECEIPT = re.compile(r"[0-9]+:(factory_insert|factory_extract):([^:]+:.+)\Z")
_CLOCK_RECEIPT = re.compile(r"[0-9]+:(.+)\Z")
_BACKGROUND_WAIT_ID = re.compile(r"background-wait:(.+)\Z")
_PLANNED_RECEIPT_KEYS = {"receipt", "planned_native_receipt_id"}
_ROUTE_DIAGNOSTIC_CLOCK_KEYS = frozenset({"cached", "survey_tick", "next_survey_tick"})
_VOLATILE_KEYS = frozenset({
    "tick", "observed_tick", "checked_tick", "last_tick", "started_tick", "finished_tick",
    "stalled_decisions", "recorded_at_utc", "timestamp", "provider_clock", "monotonic_ns",
    "duration_ms", "duration_ns", "wall_duration_ns", "process_cpu_ns", "thread_cpu_ns",
    "trace_id", "event_id", "decision_id", "model_call_id", "request_id",
})
_SYSTEM_HISTORY_EVENTS = frozenset({
    "blocked_decision_reevaluation_consumed", "blocked_recovery_attempt", "blocked_recovery_wait",
    "blocked_recovery_archive_committed",
})


def _source(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != {"commit", "source_sha256"}
            or type(value.get("commit")) is not str or not _COMMIT.fullmatch(value["commit"])
            or type(value.get("source_sha256")) is not str
            or not _SHA256.fullmatch(value["source_sha256"])):
        raise ValueError("Persistent blocked recovery requires pinned source provenance")
    return {"commit": value["commit"], "source_sha256": value["source_sha256"]}


def _canonical_receipt(value: str) -> str:
    """Remove only the current-tick component of known planned receipt forms.

    Native dispatch still receives the original receipt. The decision fingerprint
    retains action, role, item, layout, part, and other receipt identity fields.
    """
    match = _CLOCK_FACTORY_RECEIPT.fullmatch(value)
    if match is not None:
        return "<current-tick>:" + match.group(1) + ":" + match.group(2)
    match = _CLOCK_RECEIPT.fullmatch(value)
    if match is not None:
        # Planner-owned construction receipts use `tick:layout-or-target:part`.
        return "<current-tick>:" + match.group(1)
    match = re.fullmatch(r"(launch:(?:pad|load|fish):)[0-9]+", value)
    if match is not None:
        return match.group(1) + "<current-tick>"
    match = re.fullmatch(r"buffer:[0-9]+:(.+)", value)
    if match is not None:
        return "buffer:<current-tick>:" + match.group(1)
    return value


def _canonical_planned_id(value: str) -> str:
    match = _BACKGROUND_WAIT_ID.fullmatch(value)
    if match is None:
        return value
    return "background-wait:" + _canonical_receipt(match.group(1))


def _stable(value, *, path: tuple = (), current_tick: int | None = None,
            background_wait: bool = False):
    if isinstance(value, dict):
        candidate_step = (
            (len(path) == 4 and path[0] == "plans" and type(path[1]) is int
             and path[2] == "steps" and type(path[3]) is int)
            or (len(path) == 4 and path[0] == "candidate_plans"
                and type(path[1]) is str and path[2] == "steps"
                and type(path[3]) is int)
        )
        parameters = value.get("parameters")
        if (candidate_step and value.get("action") == "factory_craft_job"
                and value.get("effect") == "craft_job_complete"
                and isinstance(parameters, dict)
                and type(parameters.get("receipt")) is str
                and re.fullmatch(r"[0-9a-f]{32}", parameters["receipt"])):
            # Background compilation allocates a fresh UUID before selection.
            # Its random value is not new evidence for a rejected candidate.
            # Keep the actual dispatched/native receipt and pending jobs intact.
            value = {**value, "parameters": {
                **parameters, "receipt": "<planned-craft-job-receipt>"}}
        route_diagnostic = (
            (len(path) >= 4 and path[0] == "candidate_plans"
             and type(path[1]) is str
             and path[2:4] == ("materials", "route_diagnostics"))
            or (len(path) >= 4 and path[0] == "plans"
                and type(path[1]) is int
                and path[2:4] == ("materials", "route_diagnostics"))
            or tuple(path[:4]) == ("facts", "factory", "input_routes", "diagnostics")
        )
        background_wait = background_wait or (
            type(value.get("id")) is str and value["id"].startswith("background-wait:")
        )
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("Decision input contains a non-string key")
            normalized_key = key.casefold()
            if route_diagnostic and normalized_key in _ROUTE_DIAGNOSTIC_CLOCK_KEYS:
                # Input-route cache age is planner refresh bookkeeping. A due
                # refresh can change the substantive route evidence below, but
                # the cached bit and survey timestamps alone must not authorize
                # another model request for an unchanged native decision.
                continue
            if normalized_key in _VOLATILE_KEYS or normalized_key.endswith("_duration_ms"):
                continue
            if path == () and key == "history" and isinstance(item, list):
                # These controller-only events report polling/authorization; they
                # are intentionally not part of the Jev decision question.
                item = [event for event in item if not (
                    isinstance(event, dict) and event.get("kind") in _SYSTEM_HISTORY_EVENTS)]
            if key == "research_deadline_tick":
                if current_tick is not None and type(item) is int:
                    # This planner field is a projected deadline, computed as
                    # now plus the estimated refill horizon. Preserve that
                    # estimate rather than letting the clock alone change it.
                    result["research_horizon_ticks"] = item - current_tick
                    continue
            if (key in {"next_check_tick", "deadline_tick", "timeout_tick"}
                    or normalized_key.endswith("_deadline_tick")):
                if (current_tick is not None and type(item) is int):
                    # These are absolute scheduled horizons. Keep the horizon
                    # itself; remaining-tick encoding would change every poll.
                    canonical_key = (key if normalized_key.endswith("_deadline_tick")
                                     else key.removesuffix("_tick") + "_deadline_tick")
                    result[canonical_key] = item
                    continue
            if key == "timeout_ticks" and current_tick is not None and type(item) is int:
                # The planner expresses this wait as remaining time to the
                # durable native job deadline. Preserve that absolute horizon.
                if background_wait:
                    result["wait_absolute_deadline_tick"] = current_tick + item
                    continue
            if key in {"id", "plan_id"} and type(item) is str:
                item = _canonical_planned_id(item)
            if (key in _PLANNED_RECEIPT_KEYS and type(item) is str
                    and ("steps" in path or key == "planned_native_receipt_id")):
                item = _canonical_receipt(item)
            if (type(item) is str and path[:1] == ("candidate_evidence",)
                    and ((key == "planned_native_receipt"
                          and "utility_power_prerequisite_start_evidence" in path)
                         or (key == "native_receipt"
                             and path[-1:] == ("fuel_transfer_start_evidence",)))):
                # These witnesses describe a future paid transfer, including
                # the same witness nested under a power prerequisite. Keep
                # observed receipt journals intact; only planned clock prefixes
                # are irrelevant to an unchanged blocked decision.
                item = _canonical_receipt(item)
            result[key] = _stable(item, path=(*path, key), current_tick=current_tick,
                                  background_wait=background_wait)
        return result
    if isinstance(value, (list, tuple)):
        return [_stable(item, path=(*path, index), current_tick=current_tick,
                        background_wait=background_wait)
                for index, item in enumerate(value)]
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and value == value and abs(value) != float("inf"):
        return value
    raise ValueError("Decision input contains an unsupported value")


def decision_input_sha256(state: dict, plans: list[dict], *, session_id: str,
                          source_revision: dict, target: str, policy: str,
                          confidence_floor: float, current_tick: int) -> str:
    """Hash the actual decision facts/candidates while excluding known clocks."""
    if (type(session_id) is not str or not session_id
            or type(target) is not str or not target or policy != "jev"
            or type(current_tick) is not int or current_tick < 0
            or type(confidence_floor) not in {int, float} or not 0 <= confidence_floor <= 1
            or not isinstance(state, dict) or not isinstance(plans, list)):
        raise ValueError("Invalid persistent blocked decision input")
    payload = {
        "schema": 1, "session_id": session_id, "source_revision": _source(source_revision),
        "target": target, "policy": policy, "confidence_floor": confidence_floor,
        "state": _stable(deepcopy(state), current_tick=current_tick),
        "plans": _stable(deepcopy(plans), path=("plans",), current_tick=current_tick),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def planner_input_sha256(snapshot, plans: list[dict], blocker: str, *,
                         source_revision: dict, target: str) -> str:
    """Fingerprint a no-candidate frontier without authorizing a model call."""
    payload = {"schema": 1, "kind": "candidate_frontier", "session_id": snapshot.session_id,
               "source_revision": _source(source_revision), "target": target,
               "state": snapshot.for_jev(), "plans": plans, "blocker": blocker}
    encoded = json.dumps(_stable(payload, current_tick=snapshot.tick), sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_state(value: object, session_id: str) -> dict:
    keys = {"schema", "session_id", "source_revision", "attempts", "last_input_sha256", "wait_level"}
    if (not isinstance(value, dict) or set(value) != keys
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["session_id"] != session_id):
        raise ValueError("Invalid persistent blocked-recovery state")
    _source(value["source_revision"])
    attempts = value["attempts"]
    if not isinstance(attempts, list) or len(attempts) > MAX_ATTEMPTS:
        raise ValueError("Persistent blocked-recovery attempt ledger is invalid or full")
    seen = set()
    for row in attempts:
        legacy_required = {"source_revision", "decision_input_sha256", "reason", "tick"}
        required = legacy_required | {"outcome"}
        if not isinstance(row, dict) or frozenset(row) not in {
                frozenset(legacy_required), frozenset(required)}:
            raise ValueError("Invalid persistent blocked-recovery attempt")
        # Older local prototypes did not distinguish an in-flight call from a
        # completed rejection. Treat those rows as ambiguous and never replay.
        outcome = row.get("outcome", "pending")
        if (type(outcome) is not str
                or outcome not in {"pending", "rejected", "selected", "provider_blocked", "failed", "frontier"}
                or row["reason"] is not None and (
                    type(row["reason"]) is not str or row["reason"] not in RECOVERABLE_REASONS)
                or type(row["tick"]) is not int or row["tick"] < 0
                or type(row["decision_input_sha256"]) is not str
                or not _SHA256.fullmatch(row["decision_input_sha256"])):
            raise ValueError("Invalid persistent blocked-recovery attempt")
        row_source = _source(row["source_revision"])
        key = (row_source["commit"], row_source["source_sha256"], row["decision_input_sha256"])
        if key in seen:
            raise ValueError("Duplicate persistent blocked-recovery attempt")
        seen.add(key)
    if (value["last_input_sha256"] is not None
            and (type(value["last_input_sha256"]) is not str
                 or not _SHA256.fullmatch(value["last_input_sha256"]))):
        raise ValueError("Invalid persistent blocked-recovery fingerprint")
    if (type(value["wait_level"]) is not int
            or not 0 <= value["wait_level"] <= MAX_WAIT_LEVEL):
        raise ValueError("Invalid persistent blocked-recovery backoff")
    return value


def validate_memory_state(memory, current_source: dict, *, allow_source_change: bool = False) -> None:
    current = _source(current_source)
    if memory.blocked_recovery is None:
        if memory.status == "blocked" and memory.reason in RECOVERABLE_REASONS and not allow_source_change:
            raise ValueError("Blocked resume requires a prior durable recovery attempt or source authorization")
        return
    state = _validate_state(memory.blocked_recovery, memory.session_id)
    if (memory.status == "blocked" and not state["attempts"]
            and not allow_source_change):
        raise ValueError("First blocked recovery requires explicit changed-contract authorization")
    if state["source_revision"] != current and not (
            allow_source_change and memory.status == "blocked"
            and memory.reason in RECOVERABLE_REASONS):
        raise ValueError("Persistent blocked-recovery source changed; explicit source authorization is required")
    if memory.status == "blocked" and memory.reason not in RECOVERABLE_REASONS:
        raise ValueError("Persistent recovery does not admit this blocked reason")


def validate_checkpoint_metadata(data: object, current_source: dict, *,
                                 allow_source_change: bool = False) -> None:
    """Pre-backend CLI guard for a persistent recovery checkpoint."""
    if not isinstance(data, dict):
        raise ValueError("Persistent blocked-recovery checkpoint is malformed")
    status, reason = data.get("status"), data.get("reason")
    if status == "blocked" and reason not in RECOVERABLE_REASONS:
        raise ValueError("Persistent recovery does not admit this blocked reason")
    state = data.get("blocked_recovery")
    session_id = data.get("session_id")
    if type(session_id) is not str or not session_id:
        raise ValueError("Persistent blocked-recovery session identity is missing")
    if state is None:
        if status == "blocked" and not allow_source_change:
            raise ValueError("Blocked resume requires source-authorized first recovery")
        return
    state = _validate_state(state, session_id)
    if status == "blocked" and not state["attempts"] and not allow_source_change:
        raise ValueError("First blocked recovery requires explicit changed-contract authorization")
    if state["source_revision"] != _source(current_source) and not (
            allow_source_change and status == "blocked"
            and reason in RECOVERABLE_REASONS):
        raise ValueError("Persistent blocked-recovery source changed; explicit source authorization is required")


def ensure_state(memory, source_revision: dict, *, allow_source_change: bool = False) -> dict:
    source = _source(source_revision)
    if memory.blocked_recovery is None:
        memory.blocked_recovery = {
            "schema": 1, "session_id": memory.session_id, "source_revision": source,
            "attempts": [], "last_input_sha256": None, "wait_level": 0,
        }
    state = _validate_state(memory.blocked_recovery, memory.session_id)
    if state["source_revision"] != source:
        if not (allow_source_change and memory.status == "blocked"
                and memory.reason in RECOVERABLE_REASONS):
            raise ValueError("Persistent blocked-recovery source changed")
        state["source_revision"] = source
    return state


def was_attempted(memory, source_revision: dict, input_sha256: str, *,
                  allow_source_change: bool = False, archive_index=None) -> bool:
    source = _source(source_revision)
    if memory.blocked_recovery_archive is not None and archive_index is None:
        raise ValueError("Blocked-recovery archive index is required for fingerprint lookup")
    if archive_index is not None and archive_index.find(
            source, input_sha256, memory=memory) is not None:
        return True
    current = memory.blocked_recovery
    if (allow_source_change and current is not None
            and current.get("source_revision") != source):
        # Do not mutate source lineage merely while checking a fingerprint; the
        # source authorization and first attempt are consumed together later.
        return False
    state = ensure_state(memory, source, allow_source_change=allow_source_change)
    return any(row["source_revision"] == source
               and row["decision_input_sha256"] == input_sha256 for row in state["attempts"])


def find_attempt(memory, source_revision: dict, input_sha256: str, *, archive_index=None) -> dict | None:
    """Return a copy of one exact source-bound ledger row, if present."""
    source = _source(source_revision)
    if memory.blocked_recovery_archive is not None and archive_index is None:
        raise ValueError("Blocked-recovery archive index is required for fingerprint lookup")
    # Consult the verified archive index first so its checkpoint binding and
    # immutable files are revalidated on every lookup. The index also contains
    # a startup snapshot of the active tail for duplicate detection, but those
    # rows are mutable: their pending outcome may have been finalized since
    # index construction. Prefer the current checkpoint memory for active rows.
    archived = (archive_index.find(source, input_sha256, memory=memory)
                if archive_index is not None else None)
    if memory.blocked_recovery is not None:
        state = _validate_state(memory.blocked_recovery, memory.session_id)
        for row in state["attempts"]:
            if row["source_revision"] == source and row["decision_input_sha256"] == input_sha256:
                result = deepcopy(row)
                result.setdefault("outcome", "pending")
                return result
    return archived


def record_attempt(memory, source_revision: dict, input_sha256: str,
                   reason: str | None, tick: int, *, allow_source_change: bool = False,
                   archive_index=None) -> None:
    if (reason is not None and (type(reason) is not str or reason not in RECOVERABLE_REASONS)
            or not _SHA256.fullmatch(input_sha256)
            or type(tick) is not int or tick < 0):
        raise ValueError("Invalid persistent blocked-recovery attempt input")
    state = ensure_state(memory, source_revision, allow_source_change=allow_source_change)
    if len(state["attempts"]) >= MAX_ATTEMPTS:
        raise ValueError("Persistent blocked-recovery attempt ledger is full")
    if was_attempted(memory, source_revision, input_sha256,
                     allow_source_change=allow_source_change, archive_index=archive_index):
        raise ValueError("Persistent blocked-recovery input was already attempted")
    state["attempts"].append({"source_revision": _source(source_revision),
                              "decision_input_sha256": input_sha256,
                              "reason": reason, "tick": tick, "outcome": "pending"})
    state["last_input_sha256"] = input_sha256
    state["wait_level"] = 1


def finish_attempt(memory, source_revision: dict, input_sha256: str, outcome: str,
                   reason: str | None = None, *, archive_index=None) -> None:
    if (outcome not in {"rejected", "selected", "provider_blocked", "failed", "frontier"}
            or reason is not None and (type(reason) is not str or reason not in RECOVERABLE_REASONS)):
        raise ValueError("Invalid persistent blocked-recovery attempt outcome")
    row = find_attempt(memory, source_revision, input_sha256,
                       archive_index=archive_index)
    if row is None or row.get("outcome") != "pending":
        raise ValueError("Persistent blocked-recovery attempt is not pending")
    for stored in memory.blocked_recovery["attempts"]:
        if (stored["source_revision"] == _source(source_revision)
                and stored["decision_input_sha256"] == input_sha256):
            stored["outcome"] = outcome
            stored["reason"] = reason if reason is not None else stored["reason"]
            return
    raise ValueError("Persistent blocked-recovery attempt disappeared")


def record_wait(memory, source_revision: dict, input_sha256: str, *,
                allow_source_change: bool = False) -> float:
    if not _SHA256.fullmatch(input_sha256):
        raise ValueError("Invalid persistent blocked-recovery observation fingerprint")
    state = ensure_state(memory, source_revision, allow_source_change=allow_source_change)
    if state["last_input_sha256"] == input_sha256:
        state["wait_level"] = min(MAX_WAIT_LEVEL, state["wait_level"] + 1)
    else:
        state["wait_level"] = 1
    state["last_input_sha256"] = input_sha256
    return wait_seconds(memory)


def wait_seconds(memory) -> float:
    state = _validate_state(memory.blocked_recovery, memory.session_id)
    exponent = max(0, state["wait_level"] - 1)
    return min(MAX_WAIT_SECONDS, float(2 ** min(exponent + 1, 9)))
