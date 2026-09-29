"""Paired legacy and one-pass causal snapshot normalization fixture.

This measures local Python capture/normalization only. It does not write a log,
exercise fsync, contact Factorio, or predict native gameplay latency.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields, is_dataclass
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

from jev_factorio import causal_trace, research_log
from jev_factorio.state import GameSnapshot


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(values),
        "median_ns": statistics.median(values),
        "p95_ns": ordered[math.ceil(0.95 * len(values)) - 1],
    }


@dataclass
class NestedFixture:
    status: str
    quantity: int


def legacy_safe_payload(value: object, secrets: tuple[str, ...]) -> object:
    """Reproduce the previous asdict -> normalize -> Redactor.clean path."""
    def normalize(item):
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float:
            return item if math.isfinite(item) else {"invalid_numeric": repr(item)}
        if is_dataclass(item) and not isinstance(item, type):
            return {field.name: normalize(getattr(item, field.name)) for field in fields(item)}
        if type(item) in (list, tuple):
            return [normalize(child) for child in item]
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Causal evidence keys must be strings")
            return {key: normalize(child) for key, child in item.items()}
        return "[unsupported value]"

    redactor = research_log.Redactor({f"SECRET_{index}": secret for index, secret in enumerate(secrets)
                                     if isinstance(secret, str) and secret})
    return redactor.clean(normalize(value))


def snapshot_fixture() -> GameSnapshot:
    entities = {
        f"entity:{index}": {
            "name": "electric-mining-drill" if index % 4 == 0 else "assembling-machine-1",
            "position": {"x": index % 31, "y": index % 17},
            "inventory": {"iron-plate": index % 13, "copper-plate": index % 7},
            "status": "working" if index % 3 else "waiting_for_source_items",
        }
        for index in range(256)
    }
    return GameSnapshot(
        tick=2560,
        player_position=(32.5, 18.0),
        inventory={"iron-plate": 24, "copper-plate": 12, "coal": 8},
        nearby_resources={"iron-ore": 9.25, "copper-ore": 13.5},
        placed_entities=["electric-mining-drill"] * 32,
        power_satisfaction=0.875,
        craft_queue=["iron-gear-wheel"] * 12,
        alerts=["low-fuel"],
        session_id="mock:normalization-fixture",
        world_kind="mock",
        researched=["automation", "logistics"],
        production_rates={"iron-plate": 1.5, "copper-plate": 0.75},
        factory={"entities": entities, "api_token": "fixture-token-1234",
                 "extension_fixture": NestedFixture("working", 3)},
    )


def benchmark(samples: int = 100) -> dict:
    if type(samples) is not int or not 20 <= samples <= 10000:
        raise ValueError("samples must be between 20 and 10000")

    snapshot = snapshot_fixture()
    secrets = ("fixture-token-1234",)
    arms = {}
    outputs = []
    implementations = (
        ("legacy_asdict_and_two_pass", lambda: legacy_safe_payload(
            {"snapshot": asdict(snapshot)}, secrets)),
        ("shallow_capture_and_one_pass", lambda: research_log._safe_observation_payload(
            {"snapshot": causal_trace._snapshot_payload(snapshot)}, secrets)),
    )
    timings = {name: {"wall": [], "cpu": []} for name, _ in implementations}
    results = {}
    for _, operation in implementations:
        for _ in range(3):
            operation()

    for index in range(samples):
        paired_arms = implementations if index % 2 == 0 else tuple(reversed(implementations))
        for name, operation in paired_arms:
            wall_start, cpu_start = time.perf_counter_ns(), time.process_time_ns()
            results[name] = operation()
            timings[name]["cpu"].append(time.process_time_ns() - cpu_start)
            timings[name]["wall"].append(time.perf_counter_ns() - wall_start)

    for name, _ in implementations:
        result = results[name]
        wall, cpu = timings[name]["wall"], timings[name]["cpu"]
        encoded = research_log.canonical_bytes(result)
        outputs.append(encoded)
        arms[name] = {
            "timed_phase": "snapshot capture through normalized and redacted payload",
            "wall_ns": distribution(wall),
            "process_cpu_ns": distribution(cpu),
            "payload_bytes": len(encoded),
            "payload_sha256": hashlib.sha256(encoded).hexdigest(),
            "per_sample_operations": ({
                "snapshot_asdict_calls": 1,
                "shallow_field_maps": 0,
                "normalization_passes": 1,
                "separate_redaction_passes": 1,
                "event_writes": 0,
                "file_fsync_calls": 0,
                "directory_fsync_calls": 0,
            } if name == "legacy_asdict_and_two_pass" else {
                "snapshot_asdict_calls": 0,
                "shallow_field_maps": 1,
                "normalization_passes": 1,
                "separate_redaction_passes": 0,
                "event_writes": 0,
                "file_fsync_calls": 0,
                "directory_fsync_calls": 0,
            }),
        }

    assert outputs[0] == outputs[1], "normalization arms changed the causal payload"
    source_paths = (
        "src/jev_factorio/causal_trace.py",
        "src/jev_factorio/research_log.py",
        "benchmarks/benchmark_causal_payload_normalization.py",
    )
    repo_root = Path(__file__).resolve().parents[1]
    return {
        "schema": 1,
        "evidence_kind": "synthetic_local_causal_payload_normalization",
        "samples_per_arm": samples,
        "arms": arms,
        "same_canonical_payload": True,
        "native_speedup_inferred": False,
        "native_game_executed": False,
        "source_sha256": {
            path: hashlib.sha256((repo_root / path).read_bytes()).hexdigest()
            for path in source_paths
        },
        "limitation": "Synthetic Python snapshot only; no log write, fsync, Factorio process, or native gameplay workload.",
        "timing_note": "Canonical payload encoding and digest comparison happen once per arm outside the timed samples.",
        "pairing_note": "Each sample pair runs both arms; arm order alternates to reduce ordering bias.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100)
    print(json.dumps(benchmark(parser.parse_args().samples), sort_keys=True, indent=2, allow_nan=False))
