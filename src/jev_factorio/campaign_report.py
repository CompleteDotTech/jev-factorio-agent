"""Offline progress/profile reports, never a deployment or gameplay authority.

All elapsed rates include controller delays. Phase timers and RPC/native/helper
inclusive timers are reported separately; they must not be summed together.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from .campaign_progress import SCIENCE, counts

MAX_LINE = 8 * 1024 * 1024


def distribution(values: list[float]) -> dict:
    values = sorted(values)
    if not values:
        return {"count": 0, "p50": None, "p95": None, "sum": 0}
    return {"count": len(values), "p50": values[(len(values) - 1) // 2],
            "p95": values[math.floor(.95 * (len(values) - 1))], "sum": sum(values)}


def analyze(path: Path) -> dict:
    """Reject mixed treatments/epochs and malformed records instead of averaging."""
    samples = defaultdict(list)
    count, first, previous, latest, signature = 0, None, None, None, None
    regressions, unknown = set(), set()
    models = set()
    phase_names = {"observe", "pre_dispatch_observe", "post_dispatch_observe", "dispatch"}
    with path.open(encoding="utf-8") as stream:
        while raw := stream.readline(MAX_LINE + 1):
            if len(raw.encode("utf-8")) > MAX_LINE or not raw.endswith("\n"):
                raise ValueError("Oversized or incomplete gameplay record")
            row = json.loads(raw)
            timestamp = datetime.fromisoformat(row["recorded_at_utc"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Timezone required for gameplay timestamps")
            state = row.get("after_state") or row["state"]
            factory = state.get("factory", {})
            tick = state["tick"]
            if type(tick) is not int or tick < 0:
                raise ValueError("Invalid native tick")
            identity = {key: row.get(key) for key in ("session_id", "world_kind", "process_id",
                        "code_revision", "policy", "target", "requested_model", "acceptance_configuration", "campaign_treatment")}
            current_signature = json.dumps(identity, sort_keys=True, allow_nan=False)
            if not identity["session_id"] or signature not in {None, current_signature}:
                raise ValueError("Mixed or unidentified session, process, revision, or treatment")
            signature = current_signature
            current = {"time": timestamp, "tick": tick, "produced": counts(factory.get("produced")),
                       "consumed": counts(factory.get("consumed")), "research": factory.get("research"),
                       "research_progress": factory.get("research_progress"),
                       "researched": set(state.get("researched") or factory.get("researched") or [])}
            if previous:
                if timestamp < previous["time"] or tick < previous["tick"]:
                    raise ValueError("Time/tick regression: split the capture at its epoch boundary")
                for key in ("produced", "consumed"):
                    left, right = previous[key], current[key]
                    if left is not None and right is not None and any(right.get(k, 0) < v for k, v in left.items()):
                        regressions.add(key)
            for key in ("produced", "consumed"):
                if current[key] is None:
                    unknown.add(key)
            runtime = factory.get("acceptance_runtime", {})
            if (runtime.get("speed") != 1 or runtime.get("tick_paused") is not False
                    or runtime.get("session_id") != row["session_id"]):
                unknown.add("normal_speed_native_runtime")
            for event in row.get("phases", []):
                value = event.get("seconds")
                if (event.get("stage") in phase_names and event.get("status") == "returned"
                        and type(value) in {int, float} and math.isfinite(value) and value >= 0):
                    samples["phase:" + event["stage"]].append(value)
            for profile in row.get("observation_profiles", []):
                if profile.get("schema") != 1:
                    raise ValueError("Unsupported observation profile")
                for group in ("calls", "subcalls"):
                    for name, entry in profile.get(group, {}).items():
                        value = entry.get("total_ns")
                        if type(value) is not int or value < 0:
                            raise ValueError("Invalid observation duration")
                        samples[group + ":" + name].append(value / 1e9)
                for name, value in profile.get("native_ns", {}).items():
                    if type(value) is not int or value < 0:
                        raise ValueError("Invalid native duration")
                    samples["native:" + name].append(value / 1e9)
            if row.get("resolved_model"):
                models.add(row["resolved_model"])
            if row.get("status") in {"uncertain", "blocked"}:
                unknown.add("repair_required")
            if first is None:
                first = current
            previous, latest = current, row
            count += 1
    if first is None:
        raise ValueError("Empty gameplay capture")
    elapsed = (previous["time"] - first["time"]).total_seconds()
    deltas = {}
    for key in ("produced", "consumed"):
        if key in unknown | regressions:
            deltas[key] = None
        else:
            left, right = first[key], previous[key]
            deltas[key] = {k: right.get(k, 0) - left.get(k, 0) for k in left.keys() | right.keys()}
    consumed = deltas["consumed"]
    useful = sum(v for k, v in consumed.items() if k in SCIENCE) if consumed is not None else None
    issues = sorted(unknown | {"counter_regression:" + key for key in regressions})
    if elapsed < 1800:
        issues.append("thirty_minute_window_required")
    if identity["world_kind"] != "fle":
        issues.append("native_world_required")
    if not identity["code_revision"]:
        issues.append("source_revision_required")
    if not isinstance(identity["process_id"], str) or not identity["process_id"].strip():
        issues.append("process_identity_required")
    return {"schema": 1, "records": count, "elapsed_seconds": elapsed,
        "interval": [first["time"].isoformat(), previous["time"].isoformat()],
        "identity": identity, "resolved_models": sorted(models), "issues": issues,
        "measurement_eligible": not issues, "deployment_authorized": False,
        "native_acceptance_proven": False, "produced_delta": deltas["produced"],
        "consumed_delta": consumed,
        "science_consumed_per_actor_minute": useful * 60 / elapsed if useful is not None and elapsed else None,
        "science_consumed_per_decision": useful / (count - 1) if useful is not None and count > 1 else None,
        "technology_completions": sorted(previous["researched"] - first["researched"]),
        "timing_seconds": {key: distribution(value) for key, value in sorted(samples.items())},
        "timing_scope": "phase_samples_or_per_observation_subcall_aggregates_not_additive",
        "window_scope": "record_timestamp_interval_excludes_work_before_first_record",
        "latest_progress": latest.get("campaign_progress")}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compare(baseline: dict, treatment: dict, baseline_save: Path, treatment_save: Path) -> dict:
    """A comparable measurement is not proof of causality, safe receipts or rollout."""
    issues = []
    hashes = [file_sha256(path) for path in (baseline_save, treatment_save)]
    if hashes[0] != hashes[1]:
        issues.append("initial_save_mismatch")
    for arm in (baseline, treatment):
        if not arm["measurement_eligible"]:
            issues.append("ineligible_measurement_arm")
    for field in ("world_kind", "policy", "target", "requested_model", "acceptance_configuration", "code_revision"):
        if baseline["identity"][field] != treatment["identity"][field]:
            issues.append("uncontrolled_change:" + field)
    if baseline["resolved_models"] != treatment["resolved_models"]:
        issues.append("resolved_model_mismatch")
    effects = {}
    for metric in ("science_consumed_per_actor_minute", "science_consumed_per_decision"):
        left, right = baseline[metric], treatment[metric]
        effects[metric] = {"baseline": left, "treatment": right,
                           "difference": right - left if left is not None and right is not None else None}
    return {"schema": 1, "comparison_eligible": not issues, "issues": sorted(set(issues)),
            "save_sha256": hashes, "metrics": effects,
            "baseline": baseline, "treatment": treatment,
            "save_binding": "operator_supplied_initial_files_not_remote_world_attestation",
            "native_acceptance_proven": False, "deployment_authorized": False,
            "outstanding": ["native_receipt_and_identity_fault_matrix", "paid_ore_flow_pilot",
                            "repeated_matched_trials", "independent_review", "production_cutover_approval"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--baseline-save", type=Path)
    parser.add_argument("--treatment-save", type=Path)
    args = parser.parse_args(argv)
    if bool(args.baseline) != bool(args.baseline_save and args.treatment_save) or (
            not args.baseline and (args.baseline_save or args.treatment_save)):
        parser.error("Paired comparison requires --baseline, --baseline-save and --treatment-save")
    try:
        result = analyze(args.log)
        if args.baseline:
            result = compare(analyze(args.baseline), result, args.baseline_save, args.treatment_save)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"Invalid campaign evidence: {type(error).__name__}\n")
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
