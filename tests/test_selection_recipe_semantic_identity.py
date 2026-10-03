from copy import deepcopy
import pytest
from test_bootstrap_recipe_chain import frontier
from jev_factorio.judgments import question_batch
from jev_factorio.blocked_persistence import selection_batch_metadata, _candidate_semantic_sha256, _selection_semantic_evidence


def test_actual080_full_and_factored_candidates_have_one_semantic_identity():
    snapshot, _, plans, rows, state, _ = frontier()
    context, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert 'shared_recipes' in context and len(offered)==2
    metadata=selection_batch_metadata(context,questions,offered,state_sha256='a'*64,frontier_sha256='b'*64,current_tick=snapshot.tick)
    full={_candidate_semantic_sha256(plan.to_dict(),rows[plan.id],current_tick=snapshot.tick) for plan in plans}
    assert {row['candidate_sha256'] for row in metadata['offered']}==full
    # A refusal's offered hashes now exclude both unchanged full compiler candidates.
    assert not [plan for plan in plans if _candidate_semantic_sha256(plan.to_dict(),rows[plan.id],current_tick=snapshot.tick) not in full]
    assert _selection_semantic_evidence(context)==rows
    assert _selection_semantic_evidence(state) is state['candidate_evidence']
    changed=deepcopy(context)
    recipe=next(iter(changed['shared_recipes'].values()))
    recipe['ingredients'][0]['amount'] += 1
    altered=selection_batch_metadata(changed,questions,offered,state_sha256='a'*64,frontier_sha256='b'*64,current_tick=snapshot.tick)
    assert altered['offered']!=metadata['offered']
    assert altered['request_sha256']!=metadata['request_sha256']


@pytest.mark.parametrize('change',['missing','extra','nested','wrong_name'])
def test_malformed_factored_recipe_identity_rejects(change):
    _, _, plans, _, state, _=frontier()
    context,_,_=question_batch(state,plans,max_bytes=48000)
    key=next(iter(context['shared_recipes']))
    if change=='missing':context['shared_recipes'].pop(key)
    if change=='extra':
        def mutate(value):
            if isinstance(value,dict):
                if value.get('shared_recipe_key')==key:value['extra']=True
                else:
                    for v in value.values():mutate(v)
            elif isinstance(value,list):
                for v in value:mutate(v)
        mutate(context['candidate_evidence'])
    if change=='nested':context['shared_recipes'][key]['ingredients']=[{'shared_recipe_key':key}]
    if change=='wrong_name':context['shared_recipes'][key]['name']='foreign'
    with pytest.raises(ValueError):_selection_semantic_evidence(context)
