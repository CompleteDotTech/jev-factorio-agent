"""On/off instrumentation overhead on identical deterministic helper work."""
from __future__ import annotations
import argparse
import json
import time
from jev_factorio.latency_report import distribution
from jev_factorio.observation import ObservationProfile


def benchmark(samples=1000):
    if type(samples) is not int or not 20 <= samples <= 10000:
        raise ValueError('samples must be between 20 and 10000')
    payload = json.dumps({'inventory': {'coal': 8}, 'values': list(range(128))})
    wall={False:[],True:[]};cpu={False:[],True:[]}
    outputs=[]
    for index in range(samples):
        # Alternate order to avoid assigning warmup/drift to one treatment only.
        for enabled in ((False,True) if index%2 else (True,False)):
            began,cpu_began=time.perf_counter_ns(),time.process_time_ns()
            if enabled:
                profile=ObservationProfile()
                value=profile.subcall('entities', lambda: profile.decode(
                    profile.rpc('campaign_snapshot',lambda:payload,len(payload))))
                profile.summary()
            else:
                value=json.loads(payload)
            cpu[enabled].append(time.process_time_ns()-cpu_began)
            wall[enabled].append(time.perf_counter_ns()-began)
            outputs.append(value['inventory']['coal'])
    assert set(outputs)=={8}
    return {'schema':1,'evidence_kind':'deterministic_fixture_not_native_game',
            'samples_per_arm':samples,'workload':'same JSON response and parse; no live transport',
            'arms':{str(enabled).lower():{'wall':distribution(wall[enabled]),'process_cpu':distribution(cpu[enabled])}
                    for enabled in (False,True)},
            'native_speedup_inferred':False,'outputs_equal':True}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=1000)
    print(json.dumps(benchmark(parser.parse_args().samples),indent=2,sort_keys=True))
