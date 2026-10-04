from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_recipe_transfer_chain import frontier as prior_frontier
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.planning.bootstrap_chain import catalog_projection
from jev_factorio.judgments import _qualified_shared_parent_comparison, question_batch, select_plan


def frontier():
    snapshot,catalog,_,_,state,_=prior_frontier()
    captured=json.loads((Path(__file__).parent/'fixtures/native095_shared_parent_snapshot.json').read_text())
    for key,value in captured.items():
        if hasattr(snapshot,key):setattr(snapshot,key,deepcopy(value))
    owned=snapshot.factory['bootstrap_output'];owned['authorization_sha256']=owned['binding_id']
    for key in snapshot._bootstrap_output_ownership_witness:
        if key in owned:snapshot._bootstrap_output_ownership_witness[key]=deepcopy(owned[key])
    snapshot._coherent_observation_verified=(snapshot.session_id,snapshot.tick)
    snapshot._atomic_inventory_verified=(snapshot.session_id,snapshot.tick)
    plans=MiningOutpostPlanner(catalog,snapshot,'rocket_launch').candidates()
    rows=candidate_evidence(snapshot,catalog,plans)
    state=json.loads((Path(__file__).parent/'fixtures/native095_compiler_context.json').read_text())
    assert state['facts']['tick']==snapshot.tick and state['facts']['inventory']==snapshot.inventory
    # Trace sanitization masks this digest; restore the existing immutable binding
    # used by the bootstrap compiler fixture, without asserting native authority.
    state['facts']['factory']['bootstrap_output']['authorization_sha256']=owned['authorization_sha256']
    state['facts']['factory']['recipe_dependency_catalog']=catalog_projection(snapshot,catalog,plans)
    state['candidate_evidence']=rows
    return plans,rows,state


def test_actual095_compiler_pair_is_current_partial_and_bounded():
    plans,rows,state=frontier()
    proof=_qualified_shared_parent_comparison(state['facts'],plans,rows)
    assert proof is not None
    assert proof['parent_target']['item']=='automation-science-pack'
    assert proof['kit_branch']['physical_route_bill']['transport-belt']==5
    assert proof['kit_branch']['belt_reserve_for_twenty_logistic_science']==20
    assert proof['preference_or_execution_authorized'] is False
    context,questions,offered=question_batch(state,plans,max_bytes=48000)
    assert {p.id for p in offered}=={p.id for p in plans}
    assert context['shared_parent_comparison']==proof
    assert 'No answer or confidence is imposed' in questions['candidate']['instructions']
    assert len(json.dumps({'state':context,'questions':questions},ensure_ascii=False).encode())<=48000
    proof['parent_target']['inventory_target']=999
    assert rows[plans[0].id]['local_target']['inventory_target']==25


@pytest.mark.parametrize('mutation',['tick','session','parent_quantity','source_unit_bool','route_bill','layout','path','fuel','pickup_quantity','coefficient'])
def test_comparison_rejects_stale_mismatched_or_unqualified_pair(mutation):
    plans,rows,state=frontier();facts=state['facts'];kit=rows[plans[0].id];pickup=rows[plans[1].id]
    marker=kit['input_route_kit_parent_purpose']
    if mutation=='tick':facts['tick']+=1
    elif mutation=='session':facts['session_id']='another-session'
    elif mutation=='parent_quantity':marker['parent_local_objective']['inventory_target']+=1
    elif mutation=='source_unit_bool':marker['source_unit']=True
    elif mutation=='route_bill':marker['remaining_route_bill']['transport-belt']+=1
    elif mutation=='layout':marker['layout']='another-layout'
    elif mutation=='path':marker['parent_planner_item_path'].reverse()
    elif mutation=='fuel':facts['factory']['entities']['recipe:iron-plate']['fuel']['coal']=0
    elif mutation=='pickup_quantity':pickup['bootstrap_output_pickup_start_evidence']['planned_pickup_quantity']+=1
    elif mutation=='coefficient':kit['recipe_input_transfer_start_evidence']['recipe_dependency_chain']['edges'][0]['input_inventory_target']+=1
    assert _qualified_shared_parent_comparison(facts,plans,rows) is None


def test_no_pair_annotation_when_one_candidate_or_unrelated_parent():
    plans,rows,state=frontier()
    assert _qualified_shared_parent_comparison(state['facts'],plans[:1],rows) is None
    rows[plans[0].id]['input_route_kit_parent_purpose']=None
    assert _qualified_shared_parent_comparison(state['facts'],plans,rows) is None


def test_recorded095_low_confidence_answer_still_refuses():
    plans,rows,state=frontier()
    answers=json.loads((Path(__file__).parent/'fixtures/native095_recorded_numeric_answers.json').read_text())
    class Recorded:
        def evaluate(self,*args,**kwargs):return deepcopy(answers)
    result=select_plan(Recorded(),state,plans)
    assert result.plan_id is None and result.reason=='low choice confidence'


@pytest.mark.parametrize('mutation',['reserve_coefficient','reserve_bool','reserve_yield','reserve_absent','reserve_probability_bool','reserve_probability_fraction','reserve_type','reserve_huge','coordinated_parent','coordinated_layout','coordinated_unit','coordinated_tick','route_owner','same_quantities_other_parent'])
def test_independent_route_and_catalog_reject_coordinated_marker_changes(mutation):
    plans,rows,state=frontier();facts=state['facts'];marker=rows[plans[0].id]['input_route_kit_parent_purpose']
    annotation=plans[0].materials['input_route_kit_prerequisite']
    recipe=facts['factory']['recipe_dependency_catalog']['recipes']['logistic-science-pack']
    if mutation=='reserve_coefficient':recipe['ingredients'][0]['amount']+=1
    elif mutation=='reserve_bool':recipe['products'][0]['amount']=True
    elif mutation=='reserve_yield':recipe['products'][0]['amount']=0
    elif mutation=='reserve_absent':facts['factory']['recipe_dependency_catalog']['recipes'].pop('logistic-science-pack')
    elif mutation=='reserve_probability_bool':recipe['products'][0]['probability']=True
    elif mutation=='reserve_probability_fraction':recipe['products'][0]['probability']=0.5
    elif mutation=='reserve_type':recipe['ingredients'][0]['type']='fluid'
    elif mutation=='reserve_huge':recipe['products'][0]['amount']=10**400
    elif mutation=='coordinated_parent':
        marker['parent_local_objective']['inventory_target']=annotation['parent_local_objective']['inventory_target']=21
    elif mutation=='coordinated_layout':marker['layout']=annotation['layout']='forged-layout'
    elif mutation=='coordinated_unit':marker['source_unit']=annotation['source_unit']=2548
    elif mutation=='coordinated_tick':marker['observed_tick']=annotation['observed_tick']=facts['tick']+1
    elif mutation=='route_owner':facts['factory']['entities']['recipe:iron-plate']['unit_number']+=1
    elif mutation=='same_quantities_other_parent':marker['parent_local_objective']['item']=annotation['parent_local_objective']['item']='logistic-science-pack'
    assert _qualified_shared_parent_comparison(facts,plans,rows) is None


@pytest.mark.parametrize('mutation',['tick','session','protocol_bool','geometry','paid_prefix'])
def test_independent_full_route_projection_must_remain_valid(mutation):
    plans,rows,state=frontier();facts=state['facts']
    projection=facts['factory']['recipe_dependency_catalog']['comparison_input_route']
    route=projection['sources']['recipe:iron-plate']
    if mutation=='tick':projection['tick']+=1
    elif mutation=='session':projection['session_id']='other-session'
    elif mutation=='protocol_bool':projection['protocol']=True
    elif mutation=='geometry':route['steps'][0]['direction']=True
    elif mutation=='paid_prefix':route['parts']={'drill':{}}
    assert _qualified_shared_parent_comparison(facts,plans,rows) is None


def test_identical_full_route_is_retained_once_without_mutating_input():
    plans,rows,state=frontier();facts=state['facts']
    projection=facts['factory']['recipe_dependency_catalog']['comparison_input_route']
    facts['factory']['input_routes']=deepcopy(projection)
    before=deepcopy(state)
    context,_,offered=question_batch(state,plans,max_bytes=48000)
    assert len(offered)==2 and state==before
    assert 'comparison_input_route' not in context['facts']['factory']['recipe_dependency_catalog']
    assert context['facts']['factory']['input_routes']==projection


def test_projection_is_independent_and_invalid_route_omits_comparison_contract():
    snapshot,catalog,plans,_,_,_=prior_frontier()
    projected=catalog_projection(snapshot,catalog,plans)
    assert 'comparison_input_route' in projected
    projected['recipes']['logistic-science-pack']['products'][0]['amount']=99
    projected['comparison_input_route']['sources']['recipe:iron-plate']['source_unit']=1
    assert catalog.recipes['logistic-science-pack']['products'][0]['amount']!=99
    assert snapshot.factory['input_routes']['sources']['recipe:iron-plate']['source_unit']!=1
    snapshot.factory['input_routes']['protocol']=True
    projected=catalog_projection(snapshot,catalog,plans)
    assert 'comparison_input_route' not in projected
