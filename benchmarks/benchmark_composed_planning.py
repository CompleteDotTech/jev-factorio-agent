"""Offline current-source planning fixture. Run with PYTHONPATH=src:tests.

Compare the same fixtures/interpreter/host; these numbers are not native
latency, whole-iteration timing, or campaign throughput measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import tempfile
import time
import os
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    'src/jev_factorio/controller.py',
    'src/jev_factorio/buffer_controller.py',
    'src/jev_factorio/input_controller.py',
    'src/jev_factorio/capital_controller.py',
    'src/jev_factorio/planning/ready_work.py',
    'src/jev_factorio/planning/input_routes.py',
    'src/jev_factorio/planning/factory.py',
    'tests/test_input_route_integration.py',
    'tests/test_maintenance_progress.py',
    'benchmarks/benchmark_composed_planning.py',
)


def benchmark_input_tree_sha256():
    """Fingerprint package and test Python, including transitive fixture imports."""
    paths = [
        *(ROOT / 'src/jev_factorio').rglob('*.py'),
        *(ROOT / 'tests').rglob('*.py'),
        ROOT / 'benchmarks/benchmark_composed_planning.py',
        ROOT / 'pyproject.toml',
    ]
    digest = hashlib.sha256()
    for path in sorted(paths):
        payload = path.read_bytes()
        relative = path.relative_to(ROOT).as_posix().encode()
        digest.update(len(relative).to_bytes(4, 'big'))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, 'big'))
        digest.update(payload)
    return digest.hexdigest()


LOADED_SELECTED_SOURCE_SHA256 = {
    path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
    for path in SOURCE_FILES
}
LOADED_INPUT_TREE_SHA256 = benchmark_input_tree_sha256()

# Capture provenance before loading the measured planner and fixture modules.
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.factory import FactoryPlanner
from test_input_route_integration import RouteLoop, controller
from test_maintenance_progress import progress_scenario


def assert_inputs_unchanged():
    if (LOADED_SELECTED_SOURCE_SHA256 != {
        path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in SOURCE_FILES
    } or LOADED_INPUT_TREE_SHA256 != benchmark_input_tree_sha256()):
        raise RuntimeError('Benchmark inputs changed after provenance capture')


def distribution(values):
    values=sorted(values)
    return {'count':len(values),'median':statistics.median(values),
            'p95_nearest_rank':values[math.ceil(.95*len(values))-1]}


def benchmark(samples):
    if not 5 <= samples <= 10000:
        raise ValueError('Choose 5..10000 samples')
    assert_inputs_unchanged()
    results={}
    for name,science in [('ready_science',20),('ready_science_craft',0)]:
        wall,cpu,builders,visits,signatures=[],[],[],[],set()
        original_init,original_visit=ReadyWorkPlanner.__init__,FactoryPlanner._visit
        counts=Counter()
        def initialize(self,*args,**kwargs):
            counts['builders']+=1
            return original_init(self,*args,**kwargs)
        def expand(self,*args,**kwargs):
            counts['visits']+=1
            return original_visit(self,*args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            backend,_=progress_scenario(science=science)
            loop=controller(backend,Path(directory),kind=RouteLoop)
            # One excluded warm-up. Clock/serialization noise is not a test gate.
            loop._work_candidates(backend.state)
            with patch.object(ReadyWorkPlanner,'__init__',initialize),patch.object(FactoryPlanner,'_visit',expand):
                for _ in range(samples):
                    counts.clear()
                    w,c=time.perf_counter_ns(),time.process_time_ns()
                    plans,blocker=loop._work_candidates(backend.state)
                    cpu.append(time.process_time_ns()-c)
                    wall.append(time.perf_counter_ns()-w)
                    builders.append(counts['builders']);visits.append(counts['visits'])
                    signatures.add(json.dumps({'plans':[p.to_dict() for p in plans],'blocker':blocker},sort_keys=True))
        results[name]={'wall_ns':distribution(wall),'process_cpu_ns':distribution(cpu),
                       'planner_constructors':distribution(builders),'expansion_visits':distribution(visits),
                       'stable_frontier':len(signatures)==1,
                       'frontier':json.loads(next(iter(signatures))) if len(signatures)==1 else None}
    assert_inputs_unchanged()
    return {'schema':1,'evidence':'deterministic_fixture','native_claim':False,
            'samples':samples,'clock':'perf_counter_ns','cpu_clock':'process_time_ns',
            'environment':{'python':sys.version.split()[0],'platform':platform.platform()},
            'selected_source_sha256':LOADED_SELECTED_SOURCE_SHA256,
            'benchmark_input_tree_sha256':LOADED_INPUT_TREE_SHA256,
            'measurement_limits':['Current-source scenarios, not paired pre/post source revisions.',
                                  'Input tree hashes package/test Python, benchmark script and pyproject; external dependencies are not hashed.',
                                  'No Factorio engine, provider, network or contention-controlled host.'],
            'results':results}


def paired_benchmark(samples):
    """Compare current composition with one discarded, equivalent compilation.

    The control recreates the redundant *work* removed from the old adapter
    chain, while both arms use the same current, demand-aware scheduler. It is
    deliberately not a claim about an unmodified historical checkout.
    """
    if not 5 <= samples <= 10000:
        raise ValueError('Choose 5..10000 samples')
    assert_inputs_unchanged()
    results = {}
    for name, science in [('ready_science', 20), ('ready_science_craft', 0)]:
        measures = {arm: {'wall_ns': [], 'process_cpu_ns': [],
                          'planner_constructors': [], 'expansion_visits': []}
                    for arm in ('current', 'redundant_control')}
        original_init, original_visit = ReadyWorkPlanner.__init__, FactoryPlanner._visit
        counts = Counter()

        def initialize(self, *args, **kwargs):
            counts['builders'] += 1
            return original_init(self, *args, **kwargs)

        def expand(self, *args, **kwargs):
            counts['visits'] += 1
            return original_visit(self, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            backend, _ = progress_scenario(science=science)
            loop = controller(backend, Path(directory), kind=RouteLoop)
            compile_once = loop._compile_candidates

            def compile_with_discard(snapshot):
                compile_once(snapshot)  # The historical adapter's discarded work.
                return compile_once(snapshot)

            # Warm both arms before recording. The fixture is immutable for
            # measurements; alternate order to reduce monotonic host drift.
            loop._work_candidates(backend.state)
            with patch.object(loop, '_compile_candidates', compile_with_discard):
                loop._work_candidates(backend.state)
            with patch.object(ReadyWorkPlanner, '__init__', initialize), \
                    patch.object(FactoryPlanner, '_visit', expand):
                for index in range(samples):
                    pair = {}
                    order = ('current', 'redundant_control') if index % 2 == 0 else (
                        'redundant_control', 'current')
                    for arm in order:
                        counts.clear()
                        with patch.object(loop, '_compile_candidates',
                                          compile_with_discard if arm == 'redundant_control'
                                          else compile_once):
                            wall_start, cpu_start = time.perf_counter_ns(), time.process_time_ns()
                            plans, blocker = loop._work_candidates(backend.state)
                            cpu_elapsed = time.process_time_ns() - cpu_start
                            wall_elapsed = time.perf_counter_ns() - wall_start
                        values = measures[arm]
                        values['wall_ns'].append(wall_elapsed)
                        values['process_cpu_ns'].append(cpu_elapsed)
                        values['planner_constructors'].append(counts['builders'])
                        values['expansion_visits'].append(counts['visits'])
                        pair[arm] = json.dumps({'plans': [p.to_dict() for p in plans],
                                                'blocker': blocker}, sort_keys=True)
                    if pair['current'] != pair['redundant_control']:
                        raise RuntimeError('Paired arms changed frontier semantics')
        results[name] = {
            'arms': {arm: {key: distribution(values) for key, values in metrics.items()}
                     for arm, metrics in measures.items()},
            'paired_frontiers_equal': True,
            'paired_cpu_delta_ns': distribution([
                redundant - current for redundant, current in zip(
                    measures['redundant_control']['process_cpu_ns'],
                    measures['current']['process_cpu_ns'])]),
            'paired_wall_delta_ns': distribution([
                redundant - current for redundant, current in zip(
                    measures['redundant_control']['wall_ns'],
                    measures['current']['wall_ns'])]),
        }
    assert_inputs_unchanged()
    return {'schema': 1, 'evidence': 'paired_deterministic_fixture', 'native_claim': False,
            'samples_per_arm': samples, 'clock': 'perf_counter_ns',
            'cpu_clock': 'process_time_ns',
            'environment': {'python': sys.version.split()[0], 'platform': platform.platform(),
                            'logical_cpus': os.cpu_count()},
            'selected_source_sha256': LOADED_SELECTED_SOURCE_SHA256,
            'benchmark_input_tree_sha256': LOADED_INPUT_TREE_SHA256,
            'control': 'one discarded full current-scheduler composition before the same final composition',
            'measurement_limits': [
                'Reconstructed redundant-work control, not an unmodified historical revision.',
                'Both arms use the corrected current scheduler and the same immutable fixture.',
                'No native game, provider, network, or controlled host contention.',
                'Timing differences are descriptive; no tiny-time threshold is an acceptance gate.',
            ], 'results': results}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--samples',type=int,default=100)
    p.add_argument('--paired',action='store_true',help='compare equivalent current-scheduler control')
    args=p.parse_args()
    print(json.dumps(paired_benchmark(args.samples) if args.paired else benchmark(args.samples),
                     indent=2,sort_keys=True,allow_nan=False))
