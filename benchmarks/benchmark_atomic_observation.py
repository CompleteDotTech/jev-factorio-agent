"""Paired real Python adapter paths with fixed fake transport, not native speed."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

from jev_factorio.backends.fle import FleBackend
from jev_factorio.backends.observed_factory import ObservedFactory
from jev_factorio.backends.craft_jobs import CraftJobFactory
from jev_factorio.latency_report import distribution

ROOT = Path(__file__).resolve().parents[1]


def benchmark(samples: int = 100) -> dict:
    if type(samples) is not int or not 20 <= samples <= 10000:
        raise ValueError('samples must be between 20 and 10000')
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_atomic_observation import envelope
    env = NS(Position=lambda **kw: NS(**kw),
             Resource=NS(Water='water', CrudeOil='crude-oil'),
             Prototype=NS(BurnerMiningDrill='drill', WoodenChest='chest'))
    old = sys.modules.get('fle.env')
    sys.modules['fle.env'] = env
    try:
        arms = {}
        meanings = []
        for atomic in (False, True):
            payload = envelope()
            payload['factory']['craft_job_inventory'] = {'tick':10, 'items':{'coal':8}}
            payload['targets'] = {'iron-ore':{'name':'iron-ore','position':{'x':6,'y':8},'surface_index':1}}
            payload['anchors'] = {'water':{'name':'water','position':{'x':3,'y':6},'surface_index':1},
                'crude-oil':{'name':'crude-oil','position':{'x':5,'y':4},'surface_index':1}}
            payload['bootstrap'] = {'placed_entities':['burner-mining-drill','wooden-chest'],
                'drill':{'name':'burner-mining-drill','unit_number':51,'position':{'x':0,'y':0},
                    'drop_position':{'x':2,'y':0},'fuel':{'coal':3},'status':'working'},
                'output_connected':True,'iron_ore_collected':7,'query_limit':129}
            legacy = {key:copy.deepcopy(value) for key,value in payload.items()
                      if key in {'session_id','actor_unit','surface_index','factory','targets','cache'}}
            legacy['schema'] = 1
            wire = 'JEV_SNAPSHOT|' + json.dumps(payload if atomic else legacy)
            live = json.dumps({'tick':10,'session_id':'atomic-fixture','position':[3,4]})
            class Client:
                def send_command(self, command):
                    return wire if 'observation_snapshot' in command else live
            class Tools:
                def inspect_inventory(self, entity=None): return {'iron-ore':7} if entity else {'coal':8}
                def get_entities(self, names):
                    return [NS(name='burner-mining-drill',unit_number=51,
                        position=NS(x=0,y=0),drop_position=NS(x=2,y=0),fuel={'coal':3},status=NS(value='working')),
                        NS(name='wooden-chest',unit_number=52,position=NS(x=2,y=0))]
                def nearest(self, name): return NS(**payload['anchors'][name]['position'])
            backend=FleBackend();backend.consolidated_observations=True
            backend._instance=NS(namespace=Tools(),rcon_client=Client())
            # Count the same fair-control transport path as production instead
            # of disguising a direct Python response as an omitted request.
            class Fair:
                def call(self, *args):
                    backend._instance.rcon_client.send_command('storage.fair.observe()')
                    return copy.deepcopy(payload['controls'])
            backend._fair=Fair()
            native=ObservedFactory.__new__(ObservedFactory);native.backend=backend
            native._discovery_epoch=0;native.catalog=NS(version='2.0.77')
            if atomic: native.coherent_observation_version=2
            wrapper=CraftJobFactory.__new__(CraftJobFactory);wrapper.native=native
            backend._factory=wrapper
            wall,cpu,counts=[],[],[]
            for _ in range(samples):
                start, cstart=time.perf_counter_ns(),time.process_time_ns()
                result=backend.observe()
                cpu.append(time.process_time_ns()-cstart);wall.append(time.perf_counter_ns()-start)
                profile=backend.last_observation_profile
                counts.append({'rpc':sum(row['count'] for row in profile['calls'].values()),
                    'helpers':sum(row['count'] for row in profile['subcalls'].values())})
            meaning=result.for_jev()
            for key in ('observation_snapshot_schema','observation_query_bounds'):
                meaning['factory'].pop(key,None)
            meanings.append(meaning)
            assert all(count==counts[0] for count in counts)
            arms['atomic_v2' if atomic else 'legacy_v1']={
                'wall_ns':distribution(wall),'process_cpu_ns':distribution(cpu),
                'logical_operations_per_observation':counts[0],
                'wire_response_bytes':len(wire.encode()),
            }
        assert meanings[0]==meanings[1], 'fixture facts differ'
        paths=['src/jev_factorio/backends/fle.py','src/jev_factorio/backends/observed_factory.py',
               'src/jev_factorio/backends/atomic_observation.py','src/jev_factorio/backends/craft_jobs.py',
               'src/jev_factorio/lua/observation_v2.lua','benchmarks/benchmark_atomic_observation.py']
        return {'schema':1,'evidence_kind':'fixed_fake_transport_real_python_adapters',
            'samples_per_arm':samples,'matching_game_facts':True,'arms':arms,
            'source_sha256':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths},
            'native_speedup_inferred':False,'native_game_executed':False,
            'limitation':'Preencoded fake envelopes; no server, network, entity conversion or dynamic world workload. Native v2 adds protocol/query-bound diagnostics.'}
    finally:
        if old is None: sys.modules.pop('fle.env',None)
        else: sys.modules['fle.env']=old


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples',type=int,default=100)
    print(json.dumps(benchmark(parser.parse_args().samples),sort_keys=True,indent=2))
