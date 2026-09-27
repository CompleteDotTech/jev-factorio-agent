"""Repeatable offline outpost fuel-scope fixture; never opens a native backend.

Run from this checkout with PYTHONPATH=src:tests. To test an unmodified base,
put that base's src directory first instead. Both arms must use this same file
and the same mixed-contract fixture. Changed quantities are policy outcomes,
not an equal-work latency comparison or a native trip-saving measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
from pathlib import Path
import platform
from statistics import median
import sys
from time import perf_counter_ns, process_time_ns

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from test_outpost_fuel_dependency_scope import mixed_fixture  # noqa: E402
from jev_factorio.planning import fuel_service, mining_outposts  # noqa: E402


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)
    return {'samples': len(values), 'median_ns': median(ordered),
            'p95_ns': ordered[math.ceil(len(ordered) * .95) - 1]}


def run(repetitions: int) -> dict:
    cases = {
        'no_downstream_demand': dict(ready=100, plate_demand=0),
        'buffer_covers_demand': dict(ready=100_000, plate_demand=20),
        'carried_covers_demand': dict(ready=0, carried=20, plate_demand=20),
        'genuine_downstream_shortfall': dict(ready=0, plate_demand=20),
    }
    results = {}
    for name, options in cases.items():
        wall, cpu, observed = [], [], []
        for iteration in range(repetitions + 1):
            # Independent fresh fixtures; setup is outside the measured _need.
            state, _, planner = mixed_fixture(**options)
            start_wall, start_cpu = perf_counter_ns(), process_time_ns()
            plan = planner._need('iron-ore', 20)
            elapsed_cpu = process_time_ns() - start_cpu
            elapsed_wall = perf_counter_ns() - start_wall
            assert plan.steps[0].allowed(state)
            row = {'action': plan.steps[0].action,
                   'coal_requested': plan.steps[0].parameters['quantity'],
                   'consumer_count': plan.materials['fuel_service']['consumer_count'],
                   'combined_deficit': plan.materials['fuel_service']['combined_deficit']}
            if iteration:
                wall.append(elapsed_wall); cpu.append(elapsed_cpu); observed.append(row)
        assert all(row == observed[0] for row in observed)
        results[name] = {**observed[0], 'wall': distribution(wall), 'process_cpu': distribution(cpu)}
    return {
        'schema': 'jev-factorio.outpost-fuel-scope-fixture.v1',
        'environment': {'python': platform.python_version(), 'system': platform.system()},
        'source_sha256': {name: hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest()
                          for name, module in [('fuel_service', fuel_service), ('mining_outposts', mining_outposts)]},
        'scope': 'synthetic planner only; one excluded warmup per scenario; nearest-rank p95',
        'native_validation': False, 'deployed_source_readback': False,
        'native_trips_measured': None, 'native_science_per_wall_minute': None,
        'latency_comparison_basis': 'changed policy work; not a native or equal-work speed claim',
        'scenarios': results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 1000:
        parser.error('repetitions must be in [1, 1000]')
    print(json.dumps(run(args.repetitions), sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
