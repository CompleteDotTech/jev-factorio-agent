"""Native049 offline frontier replay; no actor dispatch or model calls."""
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import pytest
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.decision_support import scheduling_context
from jev_factorio.state import GameSnapshot
from jev_factorio.judgments import select_plan, question_batch
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.input_controller import input_loop_type
from jev_factorio.outpost_controller import outpost_loop_type

def capture():
    data=json.loads((Path(__file__).parent/'fixtures/native049-proposed-input-kit.json').read_text())
    state=GameSnapshot(**deepcopy(data['snapshot']))
    state._atomic_inventory_verified=state._coherent_observation_verified=(state.session_id,state.tick)
    catalog=Catalog.from_dict(data['catalog'])
    assert state.game_version==catalog.version=='2.0.77'
    assert data['provenance']['offline_only']
    return state,catalog,data['historical_answers']

def test_actual049_rejected_kit_keeps_truthful_manual_science_alternative():
    state,catalog,answers=capture();before=deepcopy(asdict(state))
    plans=MiningOutpostPlanner(catalog,state,'rocket_launch').candidates()
    assert len(plans)==2
    kit,manual=plans
    assert kit.steps[0].threshold==1 and manual.steps[0].threshold==20
    assert kit.materials['local_objective']['item']=='burner-inserter'
    assert 'shortages' not in kit.materials
    assert kit.materials['parent_material_shortages']=={'iron-ore':20}
    assert manual.materials['local_objective']=={'item':'automation-science-pack','inventory_target':10,'ultimate_goal':'rocket_launch'}
    assert manual.materials['raw_prerequisite']['planner_item_path']==['automation-science-pack','iron-gear-wheel','iron-plate','iron-ore']
    assert manual.materials['shortages']=={'iron-ore':20}
    assert 'input_route_kit_prerequisite' not in manual.materials
    assert all(p.steps[0].allowed(state) and not p.steps[0].satisfied(state) for p in plans)
    assert len({p.id for p in plans})==2
    class HistoricalClient:
        def evaluate(self, state, questions):return deepcopy(answers)
    kind=outpost_loop_type(input_loop_type(buffered_loop_type(HierarchicalLoop)))
    facts=kind._model_facts(object.__new__(kind),state)
    receipts=facts['factory'].pop('receipts',{})
    facts['factory'].pop('connectors',None)
    facts['factory']['native_transfer_receipt_count']=len(receipts)
    result=select_plan(HistoricalClient(),{'facts':facts,**scheduling_context(state,catalog,[kit],'rocket_launch')},[kit])
    assert result.plan_id is None and result.answers==answers  # Original rejection remains.
    batch=scheduling_context(state,catalog,plans,'rocket_launch')
    assert batch['candidate_evidence'][manual.id]['raw_prerequisite'] is not None
    assert batch['candidate_evidence'][manual.id]['local_target_completion_evidence'] is None
    request,questions,offered=question_batch({'facts':facts,**batch},plans,max_bytes=48000)
    assert offered==plans
    assert len(json.dumps({'state':request,'questions':questions},ensure_ascii=False,allow_nan=False).encode())<=48000
    assert asdict(state)==before

def test_building_route_keeps_serial_kit_priority():
    state,catalog,_=capture()
    state.factory['input_routes']['sources']['recipe:iron-plate']['state']='building'
    plans=MiningOutpostPlanner(catalog,state,'rocket_launch').candidates()
    assert len(plans)==1
    assert plans[0].materials['input_route_kit_prerequisite']['state']=='building'

@pytest.mark.parametrize('max_candidates',[1,2,8])
def test_frontier_respects_existing_candidate_budget(max_candidates):
    state,catalog,_=capture();planner=MiningOutpostPlanner(catalog,state,'rocket_launch',max_candidates=max_candidates)
    assert len(planner.candidates())<=max_candidates
    assert planner.expansions<=512

@pytest.mark.parametrize('field,value',[('session_id','other'),('tick',0),('protocol',True)])
def test_stale_or_wrong_native_route_identity_rejects_frontier(field,value):
    state,catalog,_=capture();state.factory['input_routes'][field]=value
    with pytest.raises(ValueError):MiningOutpostPlanner(catalog,state,'rocket_launch').candidates()

def test_wrong_native_source_unit_rejects_frontier():
    state,catalog,_=capture();state.factory['entities']['recipe:iron-plate']['unit_number']+=1
    with pytest.raises(ValueError):MiningOutpostPlanner(catalog,state,'rocket_launch').candidates()
