"""Actual controller/checkpoint/planner composition; the native backend is a double."""
from copy import deepcopy
import json
import pytest

from jev_factorio import coal_supply as c, solid_routes as s
from jev_factorio.coal_controller import coal_loop_type
from jev_factorio.solid_controller import solid_loop_type
from jev_factorio.memory import load_checkpoint
from jev_factorio.skills import Plan, Step
from coal_supply_fixtures import TARGETS, INTENTS, fixture, paid_source, paid_corridor
from test_solid_route_integration import FoundationScenario, native_catalog

Loop=coal_loop_type(solid_loop_type(FoundationScenario))


class Backend:
    solid_routes_supported=True
    coal_supply_supported=True

    def __init__(self):
        self.state=fixture();self.calls=[];self.observations=0;self.enabled=0
        self.lost_ack=False;self.prepared_once=False;self.ambiguous=False;self.before_observe=lambda:None
        self.post_dispatch=lambda:None;self.checkpoint=None

    def enable_factory(self): self.enabled+=1;return native_catalog()

    def observe(self):
        self.observations+=1;self.before_observe();return deepcopy(self.state)

    def execute(self, action, parameters):
        saved=json.loads(self.checkpoint.read_text())
        assert saved['pending']['action']==action
        assert saved['pending']['dispatch'] in {'prepared','ambiguous'}
        assert saved['active_plan']['steps'][0]['parameters']==parameters
        self.calls.append((action,deepcopy(parameters)))
        if action==c.COMMAND:
            rows=self.state.factory['coal_supply']['sources'];r=rows[parameters['target']]
            if self.prepared_once or self.ambiguous:
                phase='dispatching' if self.ambiguous else 'prepared'
                self.state.factory['coal_supply']['committed']=True
                for row in rows.values(): row['state']='building'
                r['pending']={'part':parameters['part'],'receipt':parameters['receipt'],'phase':phase}
                if self.ambiguous: r['state']='fault';r['reason']='ambiguous_dispatch'
                self.prepared_once=False
                raise TimeoutError('modeled native acknowledgement loss')
            paid_source(self.state,parameters)
        elif action==s.COMMAND:
            paid_corridor(self.state,parameters)
        else:
            raise AssertionError('Unexpected test mutation '+action)
        self.post_dispatch()
        if self.lost_ack: raise TimeoutError('modeled acknowledgement lost after native paid receipt')
        return 'modeled paid component'

    def act(self,action): assert action=='idle';return 'idle'


def controller(backend,path,*,resume=False,kind=Loop):
    backend.checkpoint=path/'coal-checkpoint.json'
    loop=kind(backend,target='rocket_launch',policy='deterministic',factory_scheduling='ready-work',
              tick_seconds=0,checkpoint=str(backend.checkpoint),resume_controller=resume,
              solid_intents=INTENTS,coal_targets=TARGETS)
    if not resume:
        loop.memory=loop.memory_type(backend.state.session_id,'rocket_launch',active_goal='iron_smelting',
            completed_goals={'stockpile_fuel':0,'bootstrap_mining':0},last_tick=backend.state.tick)
    return loop


def test_controller_builds_two_sources_and_both_receiving_corridors_with_durable_prefixes(tmp_path):
    backend=Backend();loop=controller(backend,tmp_path)
    count=sum(2+len(row['corridor']) for row in c.sources(backend.state).values())
    for i in range(count):
        record=loop.step()
        assert record['action'] in {c.COMMAND,s.COMMAND} and record['verified'],record
        assert len(backend.calls)==i+1
        assert backend.observations==(i+1)*3
        saved=load_checkpoint(backend.checkpoint,backend.state.session_id,'rocket_launch')
        assert saved.coal_commitments==loop.memory.coal_commitments
        assert saved.solid_commitments==loop.memory.solid_commitments
        assert saved.pending is None
    assert set(loop.memory.coal_commitments)==set(TARGETS)
    assert all(len(row['parts'])==2 for row in loop.memory.coal_commitments.values())
    assert not c.flow_complete(backend.state)  # Building doubles do NOT invent production counters.
    resumed=controller(backend,tmp_path,resume=True);resumed.step()
    assert len(backend.calls)==count


def test_controller_lost_ack_reconciles_without_duplicate_payment(tmp_path):
    backend=Backend();backend.lost_ack=True;loop=controller(backend,tmp_path)
    record=loop.step();assert not record['verified'];assert len(backend.calls)==1
    backend.lost_ack=False;resumed=controller(backend,tmp_path,resume=True)
    record=resumed.step();assert record['verified'],record
    assert len(backend.calls)==1
    assert len(resumed.memory.coal_commitments['alpha']['parts'])==1


def test_one_exact_prepared_replay_preserves_attempt_identity(tmp_path):
    backend=Backend();backend.prepared_once=True;loop=controller(backend,tmp_path)
    record=loop.step();assert not record['verified'];attempt=loop.memory.attempt['id']
    resumed=controller(backend,tmp_path,resume=True);record=resumed.step()
    assert record['verified'],record
    assert len(backend.calls)==2 and backend.calls[0]==backend.calls[1]
    assert resumed.memory.attempt_outcomes[-1]['id']==attempt


def test_repeated_prepare_ack_loss_cannot_be_replayed_without_bound(tmp_path):
    backend=Backend();backend.prepared_once=True;loop=controller(backend,tmp_path);loop.step()
    backend.prepared_once=True;loop.step()
    assert loop.memory.pending['polls']==1 and len(backend.calls)==2
    loop.step();assert len(backend.calls)==2 and loop.memory.pending is not None


def test_ambiguous_native_actor_mutation_never_replays(tmp_path):
    backend=Backend();backend.ambiguous=True;loop=controller(backend,tmp_path);loop.step()
    resumed=controller(backend,tmp_path,resume=True);resumed.step()
    assert len(backend.calls)==1 and resumed.memory.pending is not None
    assert resumed.memory.status=='uncertain'


def test_ready_science_is_not_discarded_by_coal_construction(tmp_path):
    class Production(FoundationScenario):
        compiled=0
        def _compile_candidates(self,snapshot):
            self.compiled+=1
            return [Plan('science:ready','iron_smelting','Collect ready science',
                         (Step('factory_extract','transfer',parameters={'role':'science:producer','item':'automation-science-pack',
                              'quantity':1,'receipt':'science:ready:1'}),))],''
    kind=coal_loop_type(solid_loop_type(Production))
    backend=Backend();backend.state.factory['entities']['science:producer']={
        'unit_number':999,'name':'assembling-machine-1','position':{'x':100,'y':100},'output':{'automation-science-pack':4}}
    loop=controller(backend,tmp_path,kind=kind);state=loop._observe()
    plans,_=loop._compile_candidates(state)
    assert loop.compiled==1 and any(p.id=='science:ready' for p in plans)
    assert any(p.steps[0].action==c.COMMAND for p in plans)


def test_untracked_source_adoption_is_rejected(tmp_path):
    backend=Backend();loop=controller(backend,tmp_path);state=loop._observe()
    from jev_factorio.planning.coal_supply import candidates
    paid_source(backend.state,candidates(state,'iron_smelting')[0].steps[0].parameters)
    loop.step();assert loop.memory.status=='uncertain' and not backend.calls
    assert not loop.memory.coal_commitments


def test_source_kit_is_locked_against_unrelated_spending_after_first_payment(tmp_path):
    backend=Backend();loop=controller(backend,tmp_path);loop.step()
    state=loop._observe();bill=c.remaining_kit(c.sources(state),state)
    state.inventory['wooden-chest']=bill['wooden-chest']
    spend=Step('factory_place','machine',parameters={'role':'extra','name':'wooden-chest','anchor':'iron-ore'},costs={'wooden-chest':1})
    assert not loop._step_allowed(spend,state)


@pytest.mark.parametrize('change',[
    lambda d:d.pop('coal_epoch'),
    lambda d:d['coal_targets'].reverse(),
    lambda d:d['coal_commitments'].pop('beta'),
    lambda d:d['coal_commitments']['alpha']['parts']['chest'].update(paid=True),
])
def test_resume_rejects_invalid_checkpoint_before_backend_attachment(tmp_path,change):
    backend=Backend();loop=controller(backend,tmp_path);loop.step()
    data=json.loads(backend.checkpoint.read_text());change(data);backend.checkpoint.write_text(json.dumps(data))
    calls=backend.enabled
    with pytest.raises(ValueError):controller(backend,tmp_path,resume=True)
    assert backend.enabled==calls
