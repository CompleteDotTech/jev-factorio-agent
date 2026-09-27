"""Exercise the actual Python/Lua adapter with API doubles, not a native game.

Run from the checkout with PYTHONPATH=src:tests. No server, provider, socket,
process supervisor, campaign or filesystem checkpoint is opened by this fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

from jev_factorio import solid_routes as routes
from test_solid_route_wire import wire, build_wire


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)
    return {"samples": len(values), "median_ns": statistics.median(values),
            "p95_nearest_rank_ns": ordered[math.ceil(.95 * len(values)) - 1]}


def source_digest() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = sorted([*root.joinpath("src").rglob("*.py"), *root.joinpath("src").rglob("*.lua"),
                    root / "tests/test_solid_route_wire.py", root / "tests/fixtures/solid_routes_runtime.lua",
                    Path(__file__).resolve()])
    h = hashlib.sha256()
    for path in paths:
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def run(repetitions: int = 10) -> dict:
    if type(repetitions) is not int or not 1 <= repetitions <= 1000:
        raise ValueError("Choose 1..1000 fixture repetitions")
    arms = {}
    for fuel in (False, True):
        wall, cpu, evidence = [], [], []
        for _ in range(repetitions):
            began, cpu_began = time.perf_counter_ns(), time.process_time_ns()
            factory, snapshot, lua, commands = wire(empty_arrays=True, fuel=fuel)
            row = build_wire(factory, snapshot)
            if routes.flow_complete(row["route"], row["layout"], snapshot):
                raise AssertionError("Placement was mislabeled as flow")
            # This advances only the API double's inventories/counter. There is
            # no native Factorio transport physics or elapsed campaign time here.
            for _ in range(3):
                lua.execute('''
                    local cell=select(2,next(storage.solid_routes.cells))
                    source.output.values[cell.item]=source.output.values[cell.item]-1
                    local inv=cell.target.inventory=="fuel" and target.fuel or target.input
                    inv.values[cell.item]=(inv.values[cell.item] or 0)+1
                    game.tick=game.tick+60
                ''')
                factory.observe(snapshot)
            row = next(iter(routes.routes(snapshot).values()))
            verified = routes.flow_complete(row["route"], row["layout"], snapshot)
            lua.execute("target." + ("fuel" if fuel else "input") + ".capacity=0;game.tick=game.tick+60")
            factory.observe(snapshot)
            blocked = next(iter(routes.routes(snapshot).values()))
            if not verified or routes.flow_complete(blocked["route"], blocked["layout"], snapshot):
                raise AssertionError("Flow/backpressure predicate mismatch")
            cpu.append(time.process_time_ns() - cpu_began)
            wall.append(time.perf_counter_ns() - began)
            evidence.append({"paid_components": lua.globals().paid_calls,
                "observed_sent": row["flow"]["sent"], "observed_received": row["flow"]["received"],
                "method": row["flow"]["method"], "unattributed_loss": row["flow"]["unattributed_loss"],
                "positive_samples": row["flow"]["positive_samples"],
                "backpressure_retains_history_but_not_current_flow": blocked["flow"]["received"] == 3,
                "logical_transport_calls": sum(c.startswith("/sc ") for c in commands),
                "sequential_approaches": commands.count("approach")})
        if any(row != evidence[0] for row in evidence):
            raise AssertionError("Fixture evidence changed between identical repetitions")
        arms["coal_to_fuel" if fuel else "gear_to_assembler_input"] = {
            "wall": distribution(wall), "process_cpu": distribution(cpu), "evidence": evidence[0]}
    return {"schema": 1, "evidence_kind": "python_lua52_api_doubles_not_native_game",
        "source_content_sha256": source_digest(), "repetitions_per_arm": repetitions,
        "wall_clock": "perf_counter_ns", "cpu_clock": "process_time_ns", "arms": arms,
        "native_transport_verified": False, "campaign_progress_verified": False,
        "native_speedup_inferred": False, "logical_calls_are_network_packets": False,
        "limits": "Synthetic inventory transitions; fake fair placement and transport. No native engine, real trips, science or research."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(run(args.repetitions), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
