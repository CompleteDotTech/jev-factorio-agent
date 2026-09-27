"""Retained history, active plans and deadline dispatches share one timeline."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.skills import Plan, Step
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_funding_evidence import funded_evidence, event, plan_event, decision_for, assert_rejected
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('change', ['erased', 'actions', 'unreleased', 'reversed', 'released'])
def test_initial_history_must_end_at_checkpoint_ownership(change):
    data = funded_evidence()
    rows, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    tick = initial['last_tick']
    history = [event('solid_kit_committed', proof, tick)]
    if change == 'erased':
        for checkpoint in (initial, final): checkpoint['solid_funding'] = None
        for record in rows: record['solid_funding'] = None
    elif change == 'actions': history[0]['funding']['actions'] += 1
    elif change == 'unreleased': history.append(deepcopy(history[0]))
    elif change == 'reversed':
        history[0]['tick'] = tick + 1
        initial['last_tick'] += 1
        history.append(event('solid_kit_abandoned', proof, tick, reason='kit_failure_budget'))
    else: history.append(event('solid_kit_abandoned', proof, tick, reason='kit_failure_budget'))
    initial['history'] = deepcopy(history)
    for record in rows: record['history'] = deepcopy(history)
    assert_rejected(data)


def test_active_observe_commit_cannot_be_committed_again_without_clear():
    data = funded_evidence()
    rows, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    history = []
    for index in (3, 4):
        proof['actions'] += 1
        tick = rows[index]['state']['tick']
        rows[index].update(action='observe', verified=False)
        decision_for(rows[index], proof)
        history.extend([event('solid_kit_committed', proof, tick), plan_event(proof, tick)])
        for record in rows[index:]:
            record.update(solid_funding=deepcopy(proof), history=deepcopy(history))
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


@pytest.mark.parametrize('change', [None, 'missing_failure', 'jump', 'wrong_plan'])
def test_real_first_failed_precondition_retains_funding(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    # Inject a changed admission result at the real pre-dispatch boundary;
    # _fail_plan, history, counters and checkpoint ownership remain production.
    allowed = loop._step_allowed
    loop._step_allowed = lambda step, snapshot: False if backend.observations >= 3 else allowed(step, snapshot)
    record = loop.step()
    final = asdict(loop.memory)
    assert record['action'] == 'observe' and not record['verified']
    assert not backend.calls and final['active_plan'] is None
    assert final['solid_funding'] is not None
    assert not funding_history_issues(initial, [record], final)
    if change is None: return
    failure = next(value for value in record['history'] if value['kind'] == 'plan_failed')
    if change == 'missing_failure': record['history'].remove(failure)
    elif change == 'wrong_plan': failure['plan'] = 'unrelated'
    else: record['failure_budgets'][final['solid_funding']['key'] + ':kit'] = 2
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('change', [None, 'replay', 'process', 'phase', 'plan', 'duplicate'])
def test_ordinary_deadline_dispatch_requires_fresh_owned_attempt(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    initial = asdict(loop.memory)
    proof = deepcopy(loop.memory.solid_funding)
    plan = Plan('ordinary-gears', 'rocket_launch', 'Ordinary production', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel',
             backend.state.inventory.get('iron-gear-wheel', 0) + 1,
             costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    loop._compile_candidates = lambda snapshot: ([plan], '')
    def advance():
        backend.state.tick = proof['deadline_tick'] + 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
    backend.kit_after = advance
    record = loop.step()
    final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    if change is None: return
    attempt = record['attempt_outcomes'][-1]
    if change == 'replay': attempt['id'] = initial['attempt_outcomes'][-1]['id']
    elif change == 'process': attempt['process_id'] = '1' * 32
    elif change == 'phase': attempt['dispatch_phases'] = {}
    elif change == 'plan': attempt['plan_id'] = 'unrelated'
    else:
        other = deepcopy(attempt); other['id'] = '2' * 32
        record['attempt_outcomes'].append(other)
    assert funding_history_issues(initial, [record], final)
