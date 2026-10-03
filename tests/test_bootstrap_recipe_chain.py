"""Actual080 refusals remain refusals; prospective chain proof is offline only."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_bootstrap_current_deficit import frontier as previous_frontier
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.judgments import question_batch, select_plan, _qualified_bootstrap_output_pickup


def frontier():
    snapshot, catalog, _, _, state = previous_frontier()
    captured = json.loads((Path(__file__).parent/'fixtures/bootstrap080_recorded_packet.json').read_bytes())
    request = captured['model_request_event']['payload']
    for key, value in captured['observation_event']['payload']['snapshot'].items():
        if hasattr(snapshot, key):
            setattr(snapshot, key, deepcopy(value))
    owned = snapshot.factory['bootstrap_output']
    # Restore only logger-redacted public authorization from its identical pinned binding ID.
    owned['authorization_sha256'] = owned['binding_id']
    snapshot._coherent_observation_verified = (snapshot.session_id, snapshot.tick)
    snapshot._atomic_inventory_verified = (snapshot.session_id, snapshot.tick)
    for key in snapshot._bootstrap_output_ownership_witness:
        if key in owned: snapshot._bootstrap_output_ownership_witness[key] = deepcopy(owned[key])
    plans = MiningOutpostPlanner(catalog, snapshot, 'rocket_launch').candidates()
    rows = candidate_evidence(snapshot, catalog, plans)
    state = deepcopy(request['state']);state.pop('candidate_plans', None)
    state['facts'] = snapshot.for_jev();state['candidate_evidence'] = rows
    from jev_factorio.planning.bootstrap_chain import catalog_projection
    state['facts']['factory']['recipe_dependency_catalog'] = catalog_projection(snapshot,catalog,plans)
    for key in ('acceptance_runtime','consumed','observation_snapshot_schema','observation_query_bounds','inventory_insertable_evidence','receipts','connectors'):state['facts']['factory'].pop(key,None)
    return snapshot, catalog, plans, rows, state, captured


def expand(context):
    recipes = context.get('shared_recipes', {})
    def restore(value):
        if isinstance(value, dict):
            if set(value) == {'shared_recipe_key'}:
                return deepcopy(recipes[value['shared_recipe_key']])
            return {key:restore(child) for key, child in value.items()}
        if isinstance(value, list):return [restore(child) for child in value]
        return value
    return restore(context)


def test_actual080_chain_arithmetic_and_lossless_bounded_packet():
    _, _, plans, rows, state, captured = frontier()
    assert [p.steps[0].parameters['quantity'] for p in plans] == [13, 20]
    expected = [[('transport-belt',25,13,13),('iron-plate',13,13,13)],
        [('automation-science-pack',20,20,20),('iron-gear-wheel',20,20,40),('iron-plate',40,20,20)]]
    for plan, values in zip(plans, expected):
        proof = rows[plan.id]['bootstrap_output_pickup_start_evidence']
        chain = proof['recipe_dependency_chain']
        assert [(e['product'],e['product_inventory_target'],e['batches'],e['input_inventory_target']) for e in chain['edges']] == values
        assert chain['later_completion_unverified'] is True
        assert _qualified_bootstrap_output_pickup(plan,state['facts'],rows[plan.id])
    context, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert [p.id for p in offered] == [p.id for p in plans]
    assert len(json.dumps({'state':context,'questions':questions},ensure_ascii=False).encode()) <= 48000
    restored = expand(context)
    for plan in plans:
        assert restored['candidate_evidence'][plan.id] == rows[plan.id]
        assert restored['candidate_plans'][plan.id]['materials']['bootstrap_output_pickup'] == plan.materials['bootstrap_output_pickup']
        assert restored['candidate_plans'][plan.id]['materials']['remaining']['iron-ore'] == 0
    assert restored['candidate_plans'][plans[1].id]['materials']['shortages']['iron-ore'] == 40
    answers = captured['model_response_event']['payload']['answers']
    decision = select_plan(SimpleNamespace(evaluate=lambda *args:deepcopy(answers)),state,plans,max_bytes=48000)
    assert decision.plan_id is None and decision.reason == 'Candidate evidence insufficient'
    assert decision.answers == answers


@pytest.mark.parametrize('mutation', ['yield','coefficient','carried','batches','buffer','target','path','malformed','huge','boolean','tick'])
def test_current_chain_contraries_fail_closed(mutation):
    snapshot,catalog,plans,rows,state,_ = frontier();plan=plans[0];row=deepcopy(rows[plan.id])
    proof=row['bootstrap_output_pickup_start_evidence'];edge=proof['recipe_dependency_chain']['edges'][0]
    if mutation=='yield':edge['recipe']['products'][0]['amount']=3
    elif mutation=='coefficient':edge['recipe']['ingredients'][0]['amount']=2
    elif mutation=='carried':edge['carried_product']=1
    elif mutation=='batches':edge['batches']=14
    elif mutation=='buffer':edge['buffered']=1
    elif mutation=='target':row['local_target']['inventory_target']=26
    elif mutation=='path':proof['planner_item_path'][0]='iron-gear-wheel'
    elif mutation=='malformed':edge['recipe']['ingredients']=[1]
    elif mutation=='huge':edge['recipe']['ingredients'][0]['amount']=10**400
    elif mutation=='boolean':edge['product_inventory_target']=True
    elif mutation=='tick':proof['observed_tick']-=1
    assert not _qualified_bootstrap_output_pickup(plan,state['facts'],row)


def test_original_catalog_mutation_invalidates_full_producer_recompile():
    snapshot,catalog,plans,_,_,_ = frontier();plan=plans[0]
    changed=deepcopy(catalog.recipes);changed['transport-belt']['ingredients'][0]['amount']=2
    different=replace(catalog,recipes=changed)
    assert candidate_evidence(snapshot,different,[plan])[plan.id].get('bootstrap_output_pickup_start_evidence') is None


@pytest.mark.parametrize('change', ['coordinated_recipe','catalog_tick','catalog_session','catalog_version','hand_category','stack_bound','bool_zero','boolean_probability'])
def test_independent_current_catalog_binding(change):
    _,_,plans,rows,state,_=frontier();plan=plans[1];row=deepcopy(rows[plan.id]);facts=deepcopy(state['facts'])
    chain=row['bootstrap_output_pickup_start_evidence']['recipe_dependency_chain']
    observed=facts['factory']['recipe_dependency_catalog']
    if change=='coordinated_recipe':
        # Four plates per gear still yields capped ore20; self-consistent arithmetic alone would pass.
        chain['edges'][1]['recipe']['ingredients'][0]['amount']=4
        chain['edges'][1]['input_inventory_target']=80
        chain['edges'][2]['product_inventory_target']=80
    elif change=='catalog_tick':observed['tick']-=1
    elif change=='catalog_session':observed['session_id']='foreign'
    elif change=='catalog_version':observed['version']='2.0.76'
    elif change=='hand_category':observed['hand_categories']['crafting']=False
    elif change=='stack_bound':observed['stack_sizes']['iron-ore']=1
    elif change=='bool_zero':chain['edges'][0]['buffered']=False
    elif change=='boolean_probability':
        chain['edges'][0]['recipe']['products'][0]['probability']=True
        observed['recipes']['automation-science-pack']['products'][0]['probability']=True
    assert not _qualified_bootstrap_output_pickup(plan,facts,row)


def test_current_catalog_projection_does_not_mutate_snapshot_or_catalog():
    from jev_factorio.planning.bootstrap_chain import catalog_projection
    snapshot,catalog,plans,_,_,_=frontier();before=deepcopy(snapshot.__dict__);recipes=deepcopy(catalog.recipes)
    projected=catalog_projection(snapshot,catalog,plans)
    projected['recipes']['transport-belt']['ingredients'][0]['amount']=99
    assert snapshot.__dict__==before and catalog.recipes==recipes


@pytest.mark.parametrize('field', ['shared_recipes','shared_recipe_key'])
def test_factoring_preserves_existing_reserved_fields(field):
    from jev_factorio.judgments import _factor_bootstrap_recipes
    _,_,_,_,state,_=frontier();original=deepcopy(state)
    original['history'].append({field:{'arbitrary':'existing caller value'}})
    result=_factor_bootstrap_recipes(original)
    assert json.dumps(result,sort_keys=True)==json.dumps(original,sort_keys=True)


def test_factoring_never_merges_different_recipes_with_same_name():
    from jev_factorio.judgments import _factor_bootstrap_recipes
    _,_,_,_,state,_=frontier();original=deepcopy(state)
    recipe=deepcopy(original['facts']['factory']['recipe_dependency_catalog']['recipes']['iron-plate'])
    recipe['ingredients'][0]['amount']=2
    original['extra_records']=[recipe,deepcopy(recipe)]
    assert _factor_bootstrap_recipes(original) is original


def test_factoring_full_context_roundtrip_preserves_numeric_types():
    from jev_factorio.judgments import _factor_bootstrap_recipes
    _,_,_,_,state,_=frontier();state['extra_values']=[0,0.0,False,1,1.0,True]
    factored=_factor_bootstrap_recipes(state);restored=expand(factored)
    restored.pop('shared_recipes',None)
    restored['execution_contract']=state['execution_contract']
    assert json.dumps(restored,sort_keys=True)==json.dumps(state,sort_keys=True)
