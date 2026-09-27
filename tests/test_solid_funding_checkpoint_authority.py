"""Checkpoint suffixes cannot introduce new catalog authority or erase exhaustion."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.integration_evidence import analyze_rows
from jev_factorio.solid_funding_evidence import funding_history_issues
from jev_factorio.skills import Plan, Step
from test_solid_kit_acquisition import kit_loop
from test_solid_funding_evidence import funded_evidence, event, assert_rejected


@pytest.mark.parametrize('declaration', ['copied', 'arbitrary', 'unobserved'])
def test_new_final_catalog_declaration_requires_independent_evidence(declaration):
    data = funded_evidence()
    records, _, initial, final = data
    route = next(value for value in records[0]['state']['factory']['solid_routes']['routes'].values()
                 if solid_funding.project_key(value) not in initial['solid_funding_catalogs'])
    key = solid_funding.project_key(route) if declaration != 'unobserved' else 'solid-project:' + 'a' * 64
    value = deepcopy(next(iter(initial['solid_funding_catalogs'].values())))
    value['observed_tick'] = final['last_tick']
    if declaration == 'arbitrary':
        value.update(catalog_sha256='1' * 64, acquisition_sha256='2' * 64)
    final['solid_funding_catalogs'][key] = value
    assert_rejected(data)


@pytest.mark.parametrize('budget', [0, 1])
@pytest.mark.parametrize('recommit', [False, True])
def test_initial_abandonment_cannot_erase_its_exhausted_budget(budget, recommit):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    history = [event('solid_kit_abandoned', proof, initial['last_tick'], reason='kit_failure_budget')]
    if recommit:
        history.append(event('solid_kit_committed', proof, initial['last_tick']))
    else:
        initial['solid_funding'] = final['solid_funding'] = None
        for record in records: record['solid_funding'] = None
    for checkpoint in (initial, final):
        checkpoint['history'] = deepcopy(history)
        checkpoint['failures'][proof['key'] + ':kit'] = budget
    for record in records:
        record['history'] = deepcopy(history)
        record['failure_budgets'][proof['key'] + ':kit'] = budget
    assert_rejected(data)


def test_truncated_abandonment_suffix_retains_its_known_exhausted_budget():
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    history = [event('solid_kit_abandoned', proof, initial['last_tick'], reason='kit_failure_budget')]
    for checkpoint in (initial, final):
        checkpoint.update(history=deepcopy(history), solid_funding=None)
        checkpoint['failures'][proof['key'] + ':kit'] = 2
    for record in records:
        record.update(history=deepcopy(history), solid_funding=None)
        record['failure_budgets'][proof['key'] + ':kit'] = 2
    result = analyze_rows(*data)
    assert result['measurement_checks_passed'], result['issues']


@pytest.mark.parametrize('change', ['missing', 'plan', 'source', 'policy', 'model_call', 'tick', 'event_source'])
def test_ordinary_commit_requires_its_own_selection_proof(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop.step()
    initial = asdict(loop.memory)
    ordinary = Plan('ordinary-gear', 'rocket_launch', 'Make one gear', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + 1,
            costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    compile_candidates = loop._compile_candidates
    loop._compile_candidates = lambda snapshot: ([ordinary], '')
    first = loop.step()
    loop._compile_candidates = compile_candidates
    second = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [first, second], final)
    if change == 'missing': first['decision'] = None
    elif change == 'plan': first['decision']['plan_id'] = 'unrelated-plan'
    elif change == 'source': first['decision']['source'] = 'deterministic-fallback'
    elif change == 'policy': first['policy'] = 'jev'
    elif change == 'model_call': first['model_call'] = True
    else:
        for history in (first['history'], second['history'], final['history']):
            for value in history:
                if value.get('kind') == 'plan_committed' and value.get('plan') == ordinary.id:
                    if change == 'event_source': value['source'] = None
                    else: value['tick'] = first['after_state']['tick']
    assert funding_history_issues(initial, [first, second], final)


@pytest.mark.parametrize('reason', ['kit_deadline', 'kit_action_budget'])
def test_initial_abandonment_must_not_contradict_its_intrinsic_proof(reason):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    history = [event('solid_kit_abandoned', proof, initial['last_tick'], reason=reason)]
    for checkpoint in (initial, final):
        checkpoint.update(history=deepcopy(history), solid_funding=None)
        checkpoint['failures'][proof['key'] + ':kit'] = 2
    for record in records:
        record.update(history=deepcopy(history), solid_funding=None)
        record['failure_budgets'][proof['key'] + ':kit'] = 2
    assert_rejected(data)


def test_initial_first_commit_cannot_claim_an_earlier_start():
    data = funded_evidence()
    records, _, initial, final = data
    for value in [initial['solid_funding'], final['solid_funding'], *[record['solid_funding'] for record in records]]:
        value['started_tick'] -= 1
        value['deadline_tick'] -= 1
    history = [event('solid_kit_committed', initial['solid_funding'], initial['last_tick'])]
    initial['history'] = final['history'] = deepcopy(history)
    for record in records: record['history'] = deepcopy(history)
    assert_rejected(data)
