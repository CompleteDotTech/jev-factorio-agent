"""Fresh build ownership and deferred release causes follow real controller order."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.solid_funding_evidence import funding_history_issues
from solid_routes_fixtures import row
from test_solid_kit_acquisition import kit_loop
from test_solid_funding_evidence import funded_evidence, event, plan_event, decision_for, assert_rejected


@pytest.mark.parametrize('change', ['process', 'reused_id', 'early_start', 'future_start',
                                   'dispatch_missing', 'extra_attempt'])
def test_new_paid_build_requires_current_unique_attempt(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    records = []
    for _ in range(20):
        records.append(loop.step())
        if row(backend.state)['parts']: break
    assert row(backend.state)['parts']
    assert not funding_history_issues(initial, records, asdict(loop.memory))
    record = records[-1]
    attempt = record['attempt_outcomes'][-1]
    if change == 'process': attempt['process_id'] = '1' * 32
    elif change == 'reused_id': attempt['id'] = records[-2]['attempt_outcomes'][-1]['id']
    elif change == 'early_start': attempt['started_tick'] = record['state']['tick'] - 1
    elif change == 'future_start': attempt['started_tick'] = record['after_state']['tick'] + 1
    elif change == 'dispatch_missing': attempt['dispatch_phases'] = {}
    else:
        other = deepcopy(attempt); other['id'] = '2' * 32
        record['attempt_outcomes'].append(other)
    assert funding_history_issues(initial, records, asdict(loop.memory))


@pytest.mark.parametrize('change', ['deadline', 'layout'])
def test_already_satisfied_deferred_release_preserves_original_cause(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    def satisfy_and_invalidate():
        if backend.observations == 3:
            step = loop.memory.active_plan['steps'][0]
            backend.state.inventory[step['item']] = step['threshold']
            if change == 'deadline':
                backend.state.tick += solid_funding.MAX_TICKS + 1
                backend.state.factory['solid_routes']['tick'] = backend.state.tick
            else:
                row(backend.state)['layout'] = 'changed-layout'
    backend.before_observe = satisfy_and_invalidate
    record = loop.step()
    assert record['action'] == 'verify' and record['verified'] and not backend.calls
    assert loop.memory.solid_funding is None
    release = next(value for value in record['history'] if value['kind'] == 'solid_kit_abandoned')
    assert release['reason'] == ('kit_deadline' if change == 'deadline' else 'kit_endpoint_or_layout_changed')
    assert record['failure_budgets'][release['key']] == 2
    assert not funding_history_issues(initial, [record], asdict(loop.memory))


@pytest.mark.parametrize('starting', [False, True])
def test_verify_commit_without_observed_step_effect_is_not_measurable(starting):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    tick = records[3]['state']['tick']
    if starting:
        initial['solid_funding'] = None
        for record in records[:3]: record['solid_funding'] = None
        proof.update(started_tick=tick, deadline_tick=tick + solid_funding.MAX_TICKS)
    else:
        proof['actions'] += 1
    records[3].update(action='verify', verified=True, attempt_outcomes=[])
    decision_for(records[3], proof)
    history = [event('solid_kit_committed', proof, tick), plan_event(proof, tick)]
    for record in records[3:]: record.update(solid_funding=deepcopy(proof), history=deepcopy(history))
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


@pytest.mark.parametrize('change', ['missing_step', 'no_effect', 'already_satisfied'])
def test_actual_verify_requires_its_captured_step_to_become_satisfied(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    def satisfy():
        if backend.observations == 3:
            step = loop.memory.active_plan['steps'][0]
            backend.state.inventory[step['item']] = step['threshold']
    backend.before_observe = satisfy
    record = loop.step()
    final = asdict(loop.memory)
    assert record['action'] == 'verify'
    assert not funding_history_issues(initial, [record], final)
    commit = next(value for value in record['history'] if value['kind'] == 'solid_kit_committed')
    step = commit['step']
    if change == 'missing_step': commit.pop('step')
    elif change == 'no_effect': record['after_state']['inventory'][step['item']] = record['state']['inventory'].get(step['item'], 0)
    else: step['threshold'] = record['state']['inventory'].get(step['item'], 0)
    assert funding_history_issues(initial, [record], final)


def test_captured_step_preserves_exact_ordinary_budget_scope(tmp_path):
    loop, _ = kit_loop(tmp_path)
    loop._observe()
    loop.memory.failures['factory:factory_craft:unrelated-recipe'] = 2
    initial = asdict(loop.memory)
    record = loop.step()
    assert record['verified'] and record['action'] == 'factory_craft'
    assert not funding_history_issues(initial, [record], asdict(loop.memory))
