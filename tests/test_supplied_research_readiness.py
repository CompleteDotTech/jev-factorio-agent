from copy import deepcopy
from dataclasses import replace
import pytest
from test_factory import catalog, snapshot, machine, recipe
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.decision_support import _supplied_research_start_evidence
from jev_factorio.judgments import _qualified_supplied_research, question_batch

def setup():
    data=catalog()
    data.recipes['automation-science-pack']=recipe('automation-science-pack',{'iron-plate':1})
    data.technologies['automation']={'enabled':True,'prerequisites':[], 'effects':[],
        'count':10,'energy_ticks':600,'ingredients':[{'name':'automation-science-pack','amount':1}]}
    state=snapshot(world_kind='fle',inventory={'iron-plate':10})
    state.game_version=data.version;state.factory.update(tick=state.tick,research='')
    state._coherent_observation_verified=(state.session_id,state.tick)
    state.factory['entities']['utility:lab']=machine('lab',energy=100,electric_network_id=1)
    return data,state

def plan(data,state):
    planner=FactoryPlanner(data,state,'rocket_launch')
    # Isolate the research ordering; separate existing power tests own topology.
    planner._machine=lambda *a:None;planner._powered=lambda *a:None
    return planner._research('automation')

def test_prepare_craft_and_fill_science_before_selecting_research():
    data,state=setup()
    first=plan(data,state)
    assert first.steps[0].action=='factory_craft'
    assert first.steps[0].parameters['recipe']=='automation-science-pack'
    assert _supplied_research_start_evidence(state,data,first) is None
    state.inventory={'automation-science-pack':10}
    fill=plan(data,state)
    assert fill.steps[0].action=='factory_insert'
    assert fill.steps[0].parameters['role']=='utility:lab'
    assert state.factory['research']==''
    state.factory['entities']['utility:lab']['input']={'automation-science-pack':10}
    selected=plan(data,state)
    assert selected.steps[0].action=='factory_research'
    proof=_supplied_research_start_evidence(state,data,selected)
    assert proof and proof['technology_completion_not_established'] is True
    assert _qualified_supplied_research(selected,vars(state),{'supplied_research_start_evidence':proof})
    context,questions,offered=question_batch({'facts':vars(state),'history':{},
        'candidate_evidence':{selected.id:{'supplied_research_start_evidence':proof}}},[selected],max_bytes=48000)
    for suffix in ('useful_progress','benefit','needs_observation'):
        assert 'supplied_research_start_evidence' in questions[selected.id+'/'+suffix]['instructions']

@pytest.mark.parametrize('field,value',[('observed_tick',True),('lab_unit',True),('electric_network_id',True),
    ('energy_now',True),('ingredients_per_unit',{}),('lab_input_now',{}),('technology','logistics'),('native_catalog_version','wrong')])
def test_consumer_rejects_malformed_or_crosswired_proof(field,value):
    data,state=setup();state.factory['entities']['utility:lab']['input']={'automation-science-pack':1}
    selected=plan(data,state);proof=_supplied_research_start_evidence(state,data,selected)
    proof[field]=value
    assert not _qualified_supplied_research(selected,vars(state),{'supplied_research_start_evidence':proof})

@pytest.mark.parametrize('change',['empty','power','stale','unit','current','locked','researched','queue','player','unverified','catalog'])
def test_contrary_native_research_readiness_rejects(change):
    data,state=setup();state.factory['entities']['utility:lab']['input']={'automation-science-pack':1}
    selected=plan(data,state);proof=_supplied_research_start_evidence(state,data,selected)
    assert proof
    if change=='empty':state.factory['entities']['utility:lab']['input']={}
    elif change=='power':state.factory['entities']['utility:lab']['energy']=0
    elif change=='stale':state.factory['tick']-=1
    elif change=='unit':state.factory['entities']['utility:lab']['unit_number']=True
    elif change=='current':state.factory['research']='logistics'
    elif change=='locked':data.technologies['automation']['enabled']=False
    elif change=='researched':state.researched=['automation']
    elif change=='queue':state.factory['crafting_queue']=1
    elif change=='player':state.factory['player_bound']=False
    elif change=='unverified':state._coherent_observation_verified=None
    elif change=='catalog':data=replace(data,version='mismatched')
    assert _supplied_research_start_evidence(state,data,selected) is None
    if change not in ('locked','unverified','catalog'):
        assert not _qualified_supplied_research(selected,vars(state),{'supplied_research_start_evidence':proof})
