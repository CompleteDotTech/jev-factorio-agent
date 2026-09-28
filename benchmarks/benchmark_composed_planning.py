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
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.factory import FactoryPlanner
from test_input_route_integration import RouteLoop, controller
from test_maintenance_progress import progress_scenario

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    'src/jev_factorio/controller.py',
    'src/jev_factorio/buffer_controller.py',
    'src/jev_factorio/input_controller.py',
    'src/jev_factorio/capital_controller.py',
    'src/jev_factorio/planning/ready_work.py',
    'benchmarks/benchmark_composed_planning.py',
)


def distribution(values):
    values=sorted(values)
    return {'count':len(values),'median':statistics.median(values),
            'p95_nearest_rank':values[math.ceil(.95*len(values))-1]}


def benchmark(samples):
    if not 5 <= samples <= 10000:
        raise ValueError('Choose 5..10000 samples')
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
    return {'schema':1,'evidence':'deterministic_fixture','native_claim':False,
            'samples':samples,'clock':'perf_counter_ns','cpu_clock':'process_time_ns',
            'environment':{'python':sys.version.split()[0],'platform':platform.platform()},
            'selected_source_sha256':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest()
                                      for path in SOURCE_FILES},
            'measurement_limits':['Current-source scenarios, not paired pre/post source revisions.',
                                  'No Factorio engine, provider, network or contention-controlled host.'],
            'results':results}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--samples',type=int,default=100)
    print(json.dumps(benchmark(p.parse_args().samples),indent=2,sort_keys=True,allow_nan=False))
