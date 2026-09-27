"""Paired same-state checkpoint fixture; all real fsync barriers stay enabled."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import platform
from pathlib import Path
import statistics
import tempfile
import time

from jev_factorio.memory import CampaignMemory
import jev_factorio.checkpoint_io as checkpoint


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)
    return {'count': len(values), 'median_ns': statistics.median(values),
            'p95_ns': ordered[math.ceil(0.95 * len(values)) - 1]}


def run(samples: int = 100) -> dict:
    if not 10 <= samples <= 10000:
        raise ValueError('Samples must be between 10 and 10000')
    counts = Counter()
    wall, cpu = [], []
    original_capture, original_dumps, original_sync = checkpoint.asdict, checkpoint.json.dumps, checkpoint.os.fsync

    def capture(value):
        counts['capture_calls'] += 1
        return original_capture(value)

    def dumps(*args, **kwargs):
        counts['serialization_calls'] += 1
        return original_dumps(*args, **kwargs)

    def sync(fd):
        counts['fsync_calls'] += 1
        return original_sync(fd)

    memory = CampaignMemory('checkpoint-fixture', 'rocket_launch')
    memory.history = [{'kind': 'fixture', 'counter': n, 'payload': list(range(20))}
                      for n in range(100)]
    checkpoint.asdict, checkpoint.json.dumps, checkpoint.os.fsync = capture, dumps, sync
    try:
        with tempfile.TemporaryDirectory(prefix='checkpoint-fixture-') as directory:
            destination = Path(directory) / 'checkpoint.json'
            for index in range(samples):
                # A real authoritative change every tenth call; nine exact repeats.
                memory.last_tick = index // 10
                wall_start, cpu_start = time.perf_counter_ns(), time.process_time_ns()
                memory.save(destination)
                cpu.append(time.process_time_ns() - cpu_start)
                wall.append(time.perf_counter_ns() - wall_start)
                metrics = memory._checkpoint_metrics
                counts[metrics['status']] += 1
                if metrics['status'] == 'written':
                    counts['bytes_written'] += metrics['bytes']
                for key in ('file_sync_calls', 'directory_sync_calls',
                            'parent_directory_sync_calls', 'verification_read_calls',
                            'verification_read_bytes'):
                    counts[key] += metrics.get(key, 0)
            final_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    finally:
        checkpoint.asdict, checkpoint.json.dumps, checkpoint.os.fsync = original_capture, original_dumps, original_sync
    return {'schema': 1, 'evidence_kind': 'linux_checkpoint_fixture_not_native_game',
            'python': platform.python_version(), 'samples': samples,
            'counts': dict(counts), 'wall': distribution(wall), 'process_cpu': distribution(cpu),
            'final_checkpoint_sha256': final_digest, 'fsync_enabled': True,
            'workload': '100 history entries; changed last_tick every ten calls; isolated file'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=100)
    args = parser.parse_args()
    print(json.dumps(run(args.samples), indent=2, sort_keys=True, allow_nan=False))
