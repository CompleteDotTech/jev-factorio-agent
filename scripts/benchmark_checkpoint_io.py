"""Compare exact repeated/changed checkpoints; timings are host-specific, not gameplay."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path

from jev_factorio.background import BackgroundWorkLoop
from jev_factorio.input_controller import input_loop_type
from jev_factorio.outpost_controller import outpost_loop_type


def benchmark(repeats: int = 100) -> dict:
    if type(repeats) is not int or not 1 <= repeats <= 10000:
        raise ValueError('repeats must be an integer between 1 and 10000')
    memory_type = outpost_loop_type(input_loop_type(BackgroundWorkLoop)).memory_type
    original_sync = os.fsync
    syncs = {'file': 0, 'directory': 0}

    def measured_sync(descriptor: int) -> None:
        kind = 'directory' if stat.S_ISDIR(os.fstat(descriptor).st_mode) else 'file'
        syncs[kind] += 1
        original_sync(descriptor)

    results = []
    try:
        os.fsync = measured_sync
        for changed in (False, True):
            syncs.update(file=0, directory=0)
            memory = memory_type('synthetic-benchmark', 'rocket_launch')
            memory.history = [{'kind': 'diagnostic', 'index': i, 'payload': 'x' * 100}
                              for i in range(64)]
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'state.json'
                start = time.perf_counter_ns()
                for i in range(repeats):
                    if changed:
                        memory.last_tick = i
                    memory.save(path)
                elapsed = time.perf_counter_ns() - start
                payload = path.read_bytes()
                results.append({'changed_every_save': changed, 'save_calls': repeats,
                                'fsync_calls': dict(syncs), 'elapsed_ns': elapsed,
                                'checkpoint_bytes': len(payload),
                                'final_sha256': hashlib.sha256(payload).hexdigest()})
    finally:
        os.fsync = original_sync
    return {'synthetic_only': True, 'cases': results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=100)
    args = parser.parse_args()
    try:
        print(json.dumps(benchmark(args.repeats), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(2, f'Checkpoint benchmark failed: {type(error).__name__}.\n')


if __name__ == '__main__':
    main()
