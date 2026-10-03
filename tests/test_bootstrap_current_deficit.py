"""Actual078 facts, regenerated offline evidence, unchanged recorded rejection."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import pytest
from jev_factorio.state import GameSnapshot
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.judgments import question_batch, _qualified_bootstrap_output_pickup
from jev_factorio.bootstrap_output import ROLE

def frontier():
    root=Path(__file__).parent/'fixtures'
    actual=json.loads((root/'bootstrap078_recorded_packet.json').read_text())
    # Same normal2.0.77 catalog retained in existing actual067 evidence fixture.
    from test_bootstrap_output_proof import prospective_frontier
    _,base,catalog,_,_,_=prospective_frontier()
    snapshot=GameSnapshot(**deepcopy(actual['snapshot']))
    owned=snapshot.factory['bootstrap_output'];owned['authorization_sha256']=owned['binding_id']
    snapshot._coherent_observation_verified=(snapshot.session_id,snapshot.tick)
    snapshot._atomic_inventory_verified=(snapshot.session_id,snapshot.tick)
    snapshot._bootstrap_output_ownership_witness={key:deepcopy(owned[key]) for key in ('session_id','actor_unit','surface_index','force_index','drill_unit','chest_unit','drill_position','drop_position','chest_position','origin','authorization_sha256','bound_at_tick')}
    snapshot._bootstrap_output_ownership_witness.update(schema='jev.bootstrap-output-ownership.v1',asset_sha256='b'*64)
    plans=MiningOutpostPlanner(catalog,snapshot,'rocket_launch').candidates()
    rows=candidate_evidence(snapshot,catalog,plans)
    state=deepcopy(actual['state']);state['facts']=snapshot.for_jev();state['candidate_evidence']=rows
    from jev_factorio.planning.bootstrap_chain import catalog_projection
    state['facts']['factory']['recipe_dependency_catalog'] = catalog_projection(snapshot,catalog,plans)
    for key in ('acceptance_runtime','consumed','observation_snapshot_schema','observation_query_bounds','inventory_insertable_evidence','receipts','connectors'):state['facts']['factory'].pop(key,None)
    return snapshot,catalog,plans,rows,state

def test_actual078_scoped_deficits_and_packet_bound():
    snapshot,catalog,plans,rows,state=frontier()
    assert [p.steps[0].parameters['quantity'] for p in plans]==[13,20]
    for plan in plans:
        proof=rows[plan.id]['bootstrap_output_pickup_start_evidence'];d=proof['current_raw_demand']
        assert d['carried_inventory']==0 and d['owned_source_stock']==33
        assert d['required_carried_quantity']==d['carried_deficit']==plan.steps[0].parameters['quantity']
        assert d['direct_product']=='iron-plate' and d['direct_recipe']['name']=='iron-plate'
        assert _qualified_bootstrap_output_pickup(plan,state['facts'],rows[plan.id])
    context,questions,offered=question_batch(state,plans,max_bytes=48000)
    assert len(offered)==2
    for plan in offered:
        instructions=questions[plan.id+'/useful_progress']['instructions']
        assert 'current_raw_demand' in instructions
        assert 'Allocation-ledger remaining is not carried inventory' in instructions
        assert 'their absence alone is not contrary start evidence' in instructions
    assert len(json.dumps({'state':context,'questions':questions},ensure_ascii=False).encode())<=48000

@pytest.mark.parametrize('field,value',[('carried_deficit',40),('carried_inventory',True),('observed_tick',0),('session_id','foreign'),('owned_source_stock',34),('inventory_headroom',0),('planned_pickup_quantity',34)])
def test_projection_mutations_rejected(field,value):
    snapshot,catalog,plans,rows,state=frontier();plan=plans[0]
    changed=deepcopy(rows[plan.id]);changed['bootstrap_output_pickup_start_evidence']['current_raw_demand'][field]=value
    assert not _qualified_bootstrap_output_pickup(plan,state['facts'],changed)
    materials=deepcopy(plan.materials);materials['bootstrap_output_pickup']['current_raw_demand'][field]=value
    changedplan=replace(plan,materials=materials)
    assert candidate_evidence(snapshot,catalog,[changedplan])[plan.id].get('bootstrap_output_pickup_start_evidence') is None


def test_actual078_recorded_unsupported_still_rejects():
    from types import SimpleNamespace
    from jev_factorio.judgments import select_plan
    snapshot,catalog,plans,rows,state=frontier()
    recorded=json.loads((Path(__file__).parent/'fixtures/bootstrap078_recorded_packet.json').read_text())['model_response']['answers']
    decision=select_plan(SimpleNamespace(evaluate=lambda context,questions:deepcopy(recorded)),state,plans,max_bytes=48000)
    assert decision.plan_id is None and decision.reason=='Candidate evidence insufficient'
    assert decision.answers==recorded


@pytest.mark.parametrize('stock,headroom,carried',[(5,3600,0),(33,3,0),(33,3600,5)])
def test_stock_headroom_and_carried_bounds(stock,headroom,carried):
    snapshot,catalog,_,_,_=frontier()
    owned=snapshot.factory['bootstrap_output'];owned['output']['iron-ore']=stock
    snapshot.factory['entities'][ROLE]['output']['iron-ore']=stock
    snapshot.iron_ore_collected=stock;owned['capacity']['count']=headroom
    snapshot.inventory['iron-ore']=carried
    plans=MiningOutpostPlanner(catalog,snapshot,'rocket_launch').candidates()
    rows=candidate_evidence(snapshot,catalog,plans)
    qualified=0
    for plan in plans:
        proof=rows[plan.id].get('bootstrap_output_pickup_start_evidence')
        if proof:
            qualified+=1
            d=proof['current_raw_demand']
            assert d['carried_inventory']==carried
            assert d['carried_deficit']==d['required_carried_quantity']-carried
            assert d['planned_pickup_quantity']==min(200,stock,headroom,d['carried_deficit'])
            from jev_factorio.planning.bootstrap_chain import catalog_projection
            facts=snapshot.for_jev();facts['factory']['recipe_dependency_catalog']=catalog_projection(snapshot,catalog,plans)
            assert _qualified_bootstrap_output_pickup(plan,facts,rows[plan.id])
    assert qualified>=1

@pytest.mark.parametrize('path',[None,[],['iron-ore']])
def test_malformed_path_rejects_without_exception(path):
    _,_,plans,rows,state=frontier();row=deepcopy(rows[plans[0].id])
    row['bootstrap_output_pickup_start_evidence']['planner_item_path']=path
    assert not _qualified_bootstrap_output_pickup(plans[0],state['facts'],row)


@pytest.mark.parametrize('field,value',[('ingredients',[1]),('ingredients',[{'type':'item','name':'iron-ore','amount':10**400}]),('products',None),('enabled',1),('hidden',True),('ingredients',[{'type':'item','name':'iron-ore','amount':True}]),('ingredients',[{'type':'item','name':'iron-ore','amount':float('nan')}])])
def test_malformed_recipe_shared_with_provenance_rejects(field,value):
    _,_,plans,rows,state=frontier();plan=plans[0];row=deepcopy(rows[plan.id]);materials=deepcopy(plan.materials)
    row['bootstrap_output_pickup_start_evidence']['current_raw_demand']['direct_recipe'][field]=value
    materials['bootstrap_output_pickup']['current_raw_demand']['direct_recipe'][field]=value
    assert not _qualified_bootstrap_output_pickup(replace(plan,materials=materials),state['facts'],row)


def test_recipe_projection_does_not_mutate_authoritative_catalog_or_plan():
    snapshot,catalog,plans,rows,state=frontier();plan=plans[0]
    before=deepcopy(catalog.recipe_for('iron-plate'))
    rows[plan.id]['bootstrap_output_pickup_start_evidence']['current_raw_demand']['direct_recipe']['ingredients'][0]['amount']=999
    assert plan.materials['bootstrap_output_pickup']['current_raw_demand']['direct_recipe']==before
    plan.materials['bootstrap_output_pickup']['current_raw_demand']['direct_recipe']['ingredients'][0]['amount']=999
    assert catalog.recipe_for('iron-plate')==before
    assert candidate_evidence(snapshot,catalog,[plan])[plan.id].get('bootstrap_output_pickup_start_evidence') is None
