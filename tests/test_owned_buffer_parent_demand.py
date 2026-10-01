"""Synthetic owned-buffer production paths and affordable current-target work."""
import pytest
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.decision_support import candidate_evidence
from test_output_buffer_integration import setup
from test_factory import recipe

def fixture():
    state,data,row=setup(ready=True)
    data.recipes['pipe']=recipe('pipe',{'iron-plate':1})
    state.inventory={'pipe':30,'iron-plate':8,'coal':5}
    state.nearby_resources['iron-ore']=10
    source=state.factory['entities'][row['source']]
    source.update(input={},output={},crafting=False)
    state.factory['entities'][row['chest_role']]['output']={}
    state.factory.update(player_connected=True,player_bound=True,crafting_queue=0)
    return state,data,row

def test_empty_owned_buffer_keeps_product_edge_for_gather_and_transfer():
    state,data,row=fixture()
    plan=OutputBufferPlanner(data,state,'rocket_launch')._need('pipe',41)
    assert plan.steps[0].action=='factory_gather'
    assert plan.steps[0].parameters=={'resource':'iron-ore','quantity':3}
    assert plan.materials['raw_prerequisite']['planner_item_path']==['pipe','iron-plate','iron-ore']
    evidence=candidate_evidence(state,data,[plan])[plan.id]
    assert evidence['raw_prerequisite']['direct_product']=='iron-plate'
    state.inventory['iron-ore']=3
    transfer=OutputBufferPlanner(data,state,'rocket_launch')._need('pipe',41)
    assert transfer.steps[0].action=='factory_insert'
    assert transfer.materials['recipe_input_transfer']['planner_item_path']==['pipe','iron-plate','iron-ore']

@pytest.mark.parametrize('planner_type',[ReadyWorkPlanner,OutputBufferPlanner])
def test_partial_direct_craft_uses_paid_inputs_without_claiming_target_completion(planner_type):
    state,data,_=fixture()
    planner=planner_type(data,state,'rocket_launch');planner.focus=('pipe',41)
    plan=planner._partial_current_target_craft()
    assert plan.steps[0].parameters=={'recipe':'pipe','batches':8}
    assert plan.steps[0].costs=={'iron-plate':8}
    assert plan.steps[0].threshold==38
    assert plan.materials['local_objective']['inventory_target']==41
    assert plan.materials['craft_dependency']['planner_item_path']==['pipe']
    assert plan.materials['partial_current_target_craft']['target_not_completed'] is True

@pytest.mark.parametrize('change',['queue','disconnected','bound','stock','full','speculative','buffer','locked','fluid','fractional'])
def test_partial_current_craft_fails_closed(change):
    state,data,_=fixture();planner=OutputBufferPlanner(data,state,'rocket_launch');planner.focus=('pipe',41)
    if change=='queue':state.factory['crafting_queue']=1
    elif change=='disconnected':state.factory['player_connected']=False
    elif change=='bound':state.factory['player_bound']=False
    elif change=='stock':state.inventory['iron-plate']=0
    elif change=='full':state.inventory['iron-plate']=11
    elif change=='speculative':planner.speculative=True
    elif change=='buffer':planner._buffer_service=True
    elif change=='locked':data.recipes['pipe']['enabled']=False
    elif change=='fluid':data.recipes['pipe']['ingredients'][0]['type']='fluid'
    else:state.inventory['iron-plate']=8.5
    assert planner._partial_current_target_craft() is None

def test_owned_buffer_product_cycle_rejected():
    state,data,_=fixture()
    with pytest.raises(ValueError,match='cycle'):
        OutputBufferPlanner(data,state,'rocket_launch')._need('iron-plate',11,('item:iron-plate',))

def test_partial_craft_is_offered_alongside_current_gather(monkeypatch):
    state,data,_=fixture()
    planner=OutputBufferPlanner(data,state,'rocket_launch')
    primary=planner._need('pipe',41)
    monkeypatch.setattr(planner,'plan',lambda:primary)
    planner.targets={}
    choices=planner.candidates()
    assert [p.steps[0].action for p in choices]==['factory_gather','factory_craft']
    assert choices[0].steps[0].parameters['quantity']==3
    assert choices[1].steps[0].parameters['batches']==8

@pytest.mark.parametrize('marker',['_atomic_inventory_verified','_coherent_observation_verified'])
def test_partial_native_craft_requires_same_tick_complete_inventory(marker):
    state,data,_=fixture();state.world_kind='fle'
    state._atomic_inventory_verified=state._coherent_observation_verified=(state.session_id,state.tick)
    planner=OutputBufferPlanner(data,state,'rocket_launch');planner.focus=('pipe',41)
    assert planner._partial_current_target_craft() is not None
    setattr(state,marker,(state.session_id,state.tick-1))
    assert planner._partial_current_target_craft() is None
