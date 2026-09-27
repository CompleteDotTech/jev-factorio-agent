"""Ordinary funding samples must belong to the captured executable frontier."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.skills import Plan, Step
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('change', ['missing', 'empty', 'tick', 'boundary', 'generated',
    'eligible', 'ranked', 'digest', 'definition', 'invented_id'])
def test_ordinary_commit_requires_exact_generated_frontier(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    plan = Plan('ordinary-gears', 'rocket_launch', 'Ordinary production', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + 1,
             costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    loop._compile_candidates = lambda snapshot: ([plan], '')
    initial = asdict(loop.memory); record = loop.step(); final = asdict(loop.memory)
    assert record['verified'] and not funding_history_issues(initial, [record], final)
    diagnostics = record['planning_diagnostics']
    # The synthetic baseline mutation is valid even on a revision that did not
    # yet capture frontier hashes; the old replay must not accept missing proof.
    if change == 'missing': record.pop('planning_diagnostics')
    elif change == 'empty': record['planning_diagnostics'] = {}
    elif change == 'tick': diagnostics['observed_tick'] += 1
    elif change == 'boundary': diagnostics['boundary'] = 'unobserved'
    elif change in {'generated', 'eligible', 'ranked'}: diagnostics[change + '_plan_ids'] = []
    elif change == 'digest': diagnostics['candidate_frontier'] = [{'id': plan.id, 'sha256': '0' * 64}]
    else:
        event = next(value for value in record['history'] if value.get('kind') == 'plan_committed' and value.get('plan') == plan.id)
        if change == 'definition': event['definition']['description'] = 'Uncaptured definition'
        else:
            key = 'attacker-invented-service-plan'
            event['plan'] = event['definition']['id'] = record['decision']['plan_id'] = key
            record['attempt_outcomes'][-1]['plan_id'] = key
            final['attempt_outcomes'] = deepcopy(record['attempt_outcomes'])
        final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)
