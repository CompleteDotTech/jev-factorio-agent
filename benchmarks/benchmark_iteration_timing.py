"""Paired real controller/writer timing overhead on a synthetic paid-route fixture."""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time

import jev_factorio.iteration_timing as timing
from jev_factorio.causal_trace import CausalTrace
from jev_factorio.latency_report import distribution
from jev_factorio.research_log import ResearchLog,RunConfiguration,verify_run

ROOT=Path(__file__).resolve().parents[1]


def benchmark(samples: int = 20) -> dict:
    if type(samples) is not int or not 10<=samples<=100:
        raise ValueError('samples must be between 10 and 100')
    sys.path.insert(0,str(ROOT/'tests'))
    from test_solid_route_integration import RouteBackend,controller
    enabled,fsync=timing.enabled,os.fsync
    arms={name:{'wall':[],'cpu':[],'syncs':[],'legacy_bytes':[]} for name in ('off','on')}
    def episode(directory: Path, name: str):
        directory.mkdir()
        counts={'file_sync':0,'directory_sync':0}
        def sync(fd):
            counts['directory_sync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file_sync']+=1
            return fsync(fd)
        os.fsync=sync
        timing.enabled=enabled if name=='on' else lambda loop:False
        backend=RouteBackend();loop=controller(backend,directory)
        loop.log_file=directory/'gameplay.jsonl'
        with ResearchLog(directory/'research',RunConfiguration('mock','hierarchical','deterministic'),
                         repo_dir=ROOT,environ={}) as sink:
            loop._trace=CausalTrace(sink,'hierarchical');loop._trace.metrics=loop._performance
            began,cbegan=time.perf_counter_ns(),time.process_time_ns()
            with redirect_stdout(io.StringIO()):
                for _ in range(3):
                    result=loop.step()
                    assert result['verified'] and loop.memory.pending is None
            cpu,wall=time.process_time_ns()-cbegan,time.perf_counter_ns()-began
        os.fsync=fsync
        assert verify_run(directory/'research')['complete']
        rows=[json.loads(line) for line in loop.log_file.read_text().splitlines()]
        if name=='on':
            assert len([r for r in rows if 'previous_iteration_timing' in r])==2
            for row in rows[1:]:timing.validate_timing(row['previous_iteration_timing'])
        else:
            assert all('previous_iteration_timing' not in r for r in rows)
        invariant={'native_commands':backend.calls,'gameplay':backend.observe().for_jev(),
            'paid_commitments':loop.memory.solid_commitments,'pending':loop.memory.pending,
            'reservations':loop.memory.reservations,'failures':loop.memory.failures}
        return wall,cpu,counts,loop.log_file.stat().st_size,invariant
    try:
        with tempfile.TemporaryDirectory(prefix='jev-timing-fixture-') as directory:
            for index in range(samples):
                paired={}
                for name in (('off','on') if index%2==0 else ('on','off')):
                    wall,cpu,syncs,size,invariant=episode(Path(directory)/f'{index}-{name}',name)
                    arms[name]['wall'].append(wall);arms[name]['cpu'].append(cpu)
                    arms[name]['syncs'].append(syncs);arms[name]['legacy_bytes'].append(size)
                    paired[name]=(syncs,invariant)
                assert paired['off']==paired['on'],'Paid actions/state or sync barriers changed'
        return {'schema':1,'evidence_kind':'paired_synthetic_controller_and_real_local_writers',
            'samples_per_arm':samples,'iterations_per_sample':3,'order':'alternating_off_on_pairs',
            'paid_actions_state_and_sync_counts_equal':True,
            'arms':{name:{'wall_ns':distribution(row['wall']),'process_cpu_ns':distribution(row['cpu']),
                'sync_counts_per_sample':row['syncs'],'legacy_bytes_per_sample':row['legacy_bytes']}
                for name,row in arms.items()},
            'source_sha256':{path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in (
                'src/jev_factorio/iteration_timing.py','src/jev_factorio/controller.py',
                'src/jev_factorio/research_log.py','src/jev_factorio/checkpoint_io.py',
                'src/jev_factorio/operational_safety.py','benchmarks/benchmark_iteration_timing.py')},
            'limits':['No native game, provider or network latency.',
                'On adds completed-prior timing bytes to legacy records; evidence bytes are not identical.',
                'Checkpoint attempt IDs/timestamps differ; payment, pending, reservations and failures are compared.',
                'The off arm is an offline benchmark override, not a live treatment switch.'],
            'native_speedup_inferred':False}
    finally:
        timing.enabled,os.fsync=enabled,fsync


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=20)
    print(json.dumps(benchmark(parser.parse_args().samples),indent=2,sort_keys=True))
