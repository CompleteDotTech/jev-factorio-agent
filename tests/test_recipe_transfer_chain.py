from copy import deepcopy
import json
from pathlib import Path
import pytest
from test_bootstrap_recipe_chain import frontier as prior_frontier
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.planning.bootstrap_chain import catalog_projection
from jev_factorio.judgments import question_batch, select_plan, _qualified_recipe_transfer_chain

def frontier():
 snapshot,catalog,*_=prior_frontier()
 captured=json.loads((Path(__file__).parent/'fixtures/native090_postextract_packet.json').read_bytes())
 for key,value in captured['observation_event']['payload']['snapshot'].items():
  if hasattr(snapshot,key):setattr(snapshot,key,deepcopy(value))
 owned=snapshot.factory['bootstrap_output'];owned['authorization_sha256']=owned['binding_id']
 for key in snapshot._bootstrap_output_ownership_witness:
  if key in owned:snapshot._bootstrap_output_ownership_witness[key]=deepcopy(owned[key])
 snapshot._coherent_observation_verified=(snapshot.session_id,snapshot.tick);snapshot._atomic_inventory_verified=(snapshot.session_id,snapshot.tick)
 plans=MiningOutpostPlanner(catalog,snapshot,'rocket_launch').candidates();rows=candidate_evidence(snapshot,catalog,plans)
 state=deepcopy(captured['model_request_event']['payload']['state']);state.pop('candidate_plans',None);state.pop('shared_recipes',None)
 state['facts']=snapshot.for_jev();state['facts']['factory']['recipe_dependency_catalog']=catalog_projection(snapshot,catalog,plans);state['candidate_evidence']=rows
 for key in ('acceptance_runtime','consumed','observation_snapshot_schema','observation_query_bounds','inventory_insertable_evidence','receipts','connectors'):state['facts']['factory'].pop(key,None)
 return snapshot,catalog,plans,rows,state,captured

def test_actual090_partial_transfer_chain_and_recorded_refusal():
 snapshot,catalog,plans,rows,state,captured=frontier();plan=next(p for p in plans if p.id=='factory:factory_insert:recipe:iron-plate');proof=rows[plan.id]['recipe_input_transfer_start_evidence'];chain=proof['recipe_dependency_chain']
 assert [(e['product'],e['batches'],e['input_inventory_target']) for e in chain['edges']]==[('transport-belt',13,13),('iron-plate',13,13)]
 assert proof['ingredient_in_inventory_now']==13 and proof['ingredient_in_machine_now']==0 and proof['burner_fuel_coal_now']==18
 assert chain['scope']=='selected_branch_not_full_target_bill' and _qualified_recipe_transfer_chain(plan,state['facts'],rows[plan.id])
 context,questions,offered=question_batch(state,plans,max_bytes=48000);assert {p.id for p in offered}=={p.id for p in plans};assert len(json.dumps({'state':context,'questions':questions},ensure_ascii=False).encode())<=48000
 assert 'recipe_dependency_chain' in questions[plan.id+'/useful_progress']['instructions']
 class Recorded:
  def evaluate(self,*args,**kwargs):return deepcopy(captured['model_response_event']['payload']['answers'])
 result=select_plan(Recorded(),state,plans);assert result.plan_id is None and result.reason=='low choice confidence'

@pytest.mark.parametrize('mutation',['tick','session','unit','stock','fuel','quantity','recipe','local','buffer','probability'])
def test_transfer_chain_fail_closed(mutation):
 _,_,plans,rows,state,_=frontier();plan=deepcopy(next(p for p in plans if p.id=='factory:factory_insert:recipe:iron-plate'));row=deepcopy(rows[plan.id]);facts=deepcopy(state['facts']);proof=row['recipe_input_transfer_start_evidence']
 if mutation=='tick':proof['observed_tick']+=1
 elif mutation=='session':proof['session_id']='wrong'
 elif mutation=='unit':facts['factory']['entities']['recipe:iron-plate']['unit_number']=True
 elif mutation=='stock':facts['inventory']['iron-ore']=0
 elif mutation=='fuel':facts['factory']['entities']['recipe:iron-plate']['fuel']['coal']=0
 elif mutation=='quantity':plan.steps[0].parameters['quantity']+=1
 elif mutation=='recipe':proof['recipe_dependency_chain']['edges'][0]['recipe']['ingredients'][0]['amount']+=1
 elif mutation=='local':row['local_target']['inventory_target']+=1
 elif mutation=='buffer':facts['factory']['entities']['recipe:iron-plate']['input']['iron-ore']=1
 else:proof['recipe_dependency_chain']['edges'][0]['recipe']['products'][0]['probability']=True
 assert not _qualified_recipe_transfer_chain(plan,facts,row)


def test_catalog_and_snapshot_remain_independent_of_evidence():
 snapshot,catalog,plans,rows,state,_=frontier();before=deepcopy(snapshot.__dict__);original=deepcopy(catalog.recipes)
 plan=next(p for p in plans if p.id=='factory:factory_insert:recipe:iron-plate')
 rows[plan.id]['recipe_input_transfer_start_evidence']['recipe_dependency_chain']['edges'][0]['recipe']['ingredients'][0]['amount']=999
 assert snapshot.__dict__==before and catalog.recipes==original
 assert not _qualified_recipe_transfer_chain(plan,state['facts'],rows[plan.id])

@pytest.mark.parametrize('field',['game_version','factory_tick','catalog_tick','catalog_session','catalog_version','recipe_probability','recipe_ingredients'])
def test_current_catalog_identity_and_malformed_recipe_fail_closed(field):
 _,_,plans,rows,state,_=frontier();plan=next(p for p in plans if p.id=='factory:factory_insert:recipe:iron-plate');row=deepcopy(rows[plan.id]);facts=deepcopy(state['facts']);observed=facts['factory']['recipe_dependency_catalog']
 if field=='game_version':facts['game_version']='2.0.wrong'
 elif field=='factory_tick':facts['factory']['tick']=True
 elif field=='catalog_tick':observed['tick']+=1
 elif field=='catalog_session':observed['session_id']='wrong'
 elif field=='catalog_version':observed['version']='2.0.wrong'
 else:
  for edge in row['recipe_input_transfer_start_evidence']['recipe_dependency_chain']['edges']:
   if field=='recipe_probability':edge['recipe']['products'][0]['probability']=True
   else:edge['recipe']['ingredients']=[1]
   observed['recipes'][edge['recipe']['name']]=deepcopy(edge['recipe'])
 assert not _qualified_recipe_transfer_chain(plan,facts,row)


@pytest.mark.parametrize('field',['category','burner','electric'])
def test_malformed_current_machine_prototype_rejected(field):
 _,_,plans,rows,state,_=frontier();plan=next(p for p in plans if p.id=='factory:factory_insert:recipe:iron-plate');facts=deepcopy(state['facts']);prototype=facts['factory']['recipe_dependency_catalog']['machines']['stone-furnace']
 if field=='category':prototype['categories']['smelting']='malformed'
 else:prototype[field]=1
 assert not _qualified_recipe_transfer_chain(plan,facts,rows[plan.id])


def test_machine_projection_has_no_catalog_alias():
 snapshot,catalog,plans,rows,state,_=frontier();original=deepcopy(catalog.machines)
 state['facts']['factory']['recipe_dependency_catalog']['machines']['stone-furnace']['categories']['smelting']=False
 assert catalog.machines==original
