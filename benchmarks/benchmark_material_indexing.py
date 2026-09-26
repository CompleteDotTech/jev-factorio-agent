"""Reproducible paired microbenchmark; never labels this as production evidence.

Run: PYTHONPATH=src python benchmarks/benchmark_material_indexing.py --output /private/result.json
"""
from __future__ import annotations

import argparse
import cProfile
import dataclasses
import hashlib
import io
import json
import math
import os
import platform
import pstats
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from catalog_fixture import fixture, load_reference
from jev_factorio.planning.catalog import Catalog

BASE_REVISION = 'e3d042c1f32cededfc924d4814224fe97133461f'


def summary(samples):
    rows = sorted(samples)
    return {'n': len(rows), 'median_ms': statistics.median(rows),
            'p95_ms': rows[math.ceil(0.95 * len(rows)) - 1],
            'min_ms': rows[0], 'max_ms': rows[-1]}


def profile(call):
    timer = cProfile.Profile()
    timer.runcall(call)
    out = io.StringIO()
    pstats.Stats(timer, stream=out).strip_dirs().sort_stats('cumulative').print_stats(16)
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=31)
    parser.add_argument('--sizes', nargs='+', type=int, default=[128, 384, 768])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 3 <= args.samples <= 1000:
        parser.error('--samples must be 3..1000')
    before_catalog, _ = load_reference()
    started = datetime.now(timezone.utc).isoformat()
    report = {'schema': 1, 'evidence_kind': 'offline_synthetic_paired_microbenchmark',
              'production_measurement': False, 'base_revision': BASE_REVISION,
              'python': sys.version, 'platform': platform.platform(),
              'visible_cpus': os.cpu_count(),
              'cpu_affinity_count': len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
              'timing': 'perf_counter_ns; alternating AB/BA; 2 warmups; all index construction included',
              'started_at': started, 'cases': []}
    source = Path(__file__).resolve().parents[1] / 'src/jev_factorio/planning'
    report['candidate_sha256'] = {n: hashlib.sha256((source / n).read_bytes()).hexdigest()
                                  for n in ('catalog.py', 'materials.py')}
    for size in args.sizes:
        data, demand, inventory, researched = fixture(size, seed=1729)
        catalogs = [before_catalog.Catalog.from_dict(data), Catalog.from_dict(data)]
        calls = [lambda c=c: c.material_demands(demand, inventory, researched) for c in catalogs]
        assert dataclasses.asdict(calls[0]()) == dataclasses.asdict(calls[1]())
        for call in calls:
            for _ in range(2):
                call()
        samples = [[], []]
        for pair in range(args.samples):
            for arm in ([0, 1] if pair % 2 == 0 else [1, 0]):
                start = time.perf_counter_ns()
                calls[arm]()
                samples[arm].append((time.perf_counter_ns() - start) / 1_000_000)
        fixture_json = json.dumps([data, demand, inventory, researched], sort_keys=True).encode()
        case = {'recipes': size, 'technologies': size, 'demand_items': len(demand),
                'fixture_sha256': hashlib.sha256(fixture_json).hexdigest(),
                'before': summary(samples[0]), 'after': summary(samples[1]),
                'raw_ms': {'before': samples[0], 'after': samples[1]},
                'profile_before': profile(calls[0]), 'profile_after': profile(calls[1])}
        case['median_ratio_before_over_after'] = case['before']['median_ms'] / case['after']['median_ms']
        report['cases'].append(case)
        print(json.dumps({k: v for k, v in case.items() if k not in {'raw_ms', 'profile_before', 'profile_after'}}), flush=True)
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Evidence is a fresh file, not an overwrite of an earlier attempt.
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
