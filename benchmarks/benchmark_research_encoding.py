"""Matched durable V1 event writes; payload encoded twice versus once."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from datetime import datetime, timezone
from uuid import UUID

import jev_factorio.research_log as log
from jev_factorio.latency_report import distribution

ROOT=Path(__file__).resolve().parents[1]


def reference(event):
    event['event_hash']=log.digest(event)
    log.validate_event(event)
    data=log.canonical_bytes(event)+b'\n'
    if len(data)>log.MAX_RECORD_BYTES:raise ValueError('Evidence event exceeds V1 size limit')
    return data


def benchmark(samples=50):
    if type(samples) is not int or not 20<=samples<=1000:raise ValueError('Invalid sample count')
    encoder,uid,dumps,sync=log._encode_event,log.uuid.uuid4,json.dumps,os.fsync
    arms,contents={},[]
    try:
        log.uuid.uuid4=lambda:UUID('cafb9fe3-8ba4-4cc2-ae74-a3a8fe55bd49')
        with tempfile.TemporaryDirectory(prefix='jev-encoding-fixture-') as directory:
            for name,function in [('reference_v1',reference),('reused_value_bytes',encoder)]:
                counts={'payload_serializations':0,'file_sync':0,'directory_sync':0}
                def encode(value,*args,**kwargs):
                    if isinstance(value,dict) and ('large_payload' in value or
                            isinstance(value.get('payload'),dict) and 'large_payload' in value['payload']):
                        counts['payload_serializations']+=1
                    return dumps(value,*args,**kwargs)
                def fsync(fd):
                    counts['directory_sync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file_sync']+=1
                    return sync(fd)
                json.dumps,os.fsync,log._encode_event=encode,fsync,function
                path=Path(directory)/name
                sink=log.ResearchLog(path,log.RunConfiguration('mock','hierarchical','deterministic'),
                    repo_dir=ROOT,environ={},monotonic_ns=lambda:1,
                    utc_now=lambda:datetime(2026,1,1,tzinfo=timezone.utc))
                wall,cpu=[],[]
                for index in range(samples):
                    payload={'large_payload':[{'item':'iron-plate','amount':value,'index':index}
                                             for value in range(512)]}
                    start,cstart=time.perf_counter_ns(),time.process_time_ns()
                    sink.emit('observation',payload)
                    cpu.append(time.process_time_ns()-cstart);wall.append(time.perf_counter_ns()-start)
                sink.finish()
                writer_counts = dict(counts)
                # Verification still uses the independent full-event encoder.
                assert log.verify_run(path)['complete']
                content=tuple((path/f).read_bytes() for f in ('manifest.json','events.jsonl','integrity.json'))
                contents.append(content)
                arms[name]={'wall_ns':distribution(wall),'process_cpu_ns':distribution(cpu),
                    'counts':writer_counts,'event_bytes':len(content[1]),'events':samples+2}
                json.dumps,os.fsync=dumps,sync
        assert contents[0]==contents[1]
        assert arms['reference_v1']['counts']['file_sync']==arms['reused_value_bytes']['counts']['file_sync']
        return {'schema':1,'evidence_kind':'matched_local_durable_writer_fixture',
            'samples_per_arm':samples,'arms':arms,'all_artifact_bytes_equal':True,
            'authoritative_sync_barriers_equal':True,'native_speedup_inferred':False,
            'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                ('src/jev_factorio/research_log.py','benchmarks/benchmark_research_encoding.py')},
            'limit':'Local temporary filesystem and synthetic payload; not native gameplay overhead.'}
    finally:
        log._encode_event,log.uuid.uuid4,json.dumps,os.fsync=encoder,uid,dumps,sync


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--samples',type=int,default=50)
    print(json.dumps(benchmark(parser.parse_args().samples),sort_keys=True,indent=2))
