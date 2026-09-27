"""Source-bound review reproduction; fixtures are not native acceptance.

Run from a verified repository checkout with its src/ and tests/ on PYTHONPATH.
Rejection cases are checked against exact 4d7efdc4 with current fixture contracts.
The remaining cases exercise real controllers with explicit deterministic doubles.
"""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.integration_evidence import analyze_rows
from jev_factorio.skills import Plan, Step
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_funding_evidence import funded_evidence, event, plan_event, decision_for
from test_solid_kit_acquisition import kit_loop


def increment_evidence(active_step=None):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    proof['actions'] += 1
    tick = records[0]['state']['tick']
    records[0].update(action='verify', verified=True)
    decision_for(records[0], proof)
    history = [event('solid_kit_committed', proof, tick), plan_event(proof, tick)]
    for record in records:
        record.update(history=deepcopy(history), solid_funding=deepcopy(proof))
    final['solid_funding'] = deepcopy(proof)
    # Contract added in 7b680d69: durable final history matches the last record.
    final['history'] = deepcopy(history)
    # Current final-activity checks require the newly selected step to finish.
    # Use the controller's existing-effect/no-dispatch path, not a bare label.
    selected_step = Step(**deepcopy(history[0]['step']))
    assert selected_step.effect == 'inventory'
    for index, record in enumerate(records):
        for label in (('after_state',) if index == 0 else ('state', 'after_state')):
            record[label]['inventory'][selected_step.item] = selected_step.threshold
    if active_step is not None:
        # Both remaining thresholds are unsatisfied throughout the fixture;
        # no completion, failure, goal change or release is recorded.
        steps = tuple(Step('factory_craft', 'inventory', 'iron-gear-wheel', 500 + i,
                      costs={'iron-plate': 2},
                      parameters={'recipe': 'iron-gear-wheel', 'batches': 1})
                      for i in range(active_step + 1))
        plan = Plan('ordinary-production', 'rocket_launch',
                    'Retained unfinished ordinary production', steps)
        initial.update(active_plan=asdict(plan), active_goal=plan.goal, step_index=active_step)
    return data


def test_no_active_plan_control_remains_measurable():
    result = analyze_rows(*increment_evidence())
    assert result['integrity_checks_passed'], result['issues']
    assert result['measurement_checks_passed'], result['issues']
    assert result['native_acceptance'] == 'not_accepted'
    assert result['deployment_authorized'] is False


@pytest.mark.parametrize('active_step', [0, 1])
def test_kit_commit_cannot_replace_unfinished_ordinary_plan(active_step):
    result = analyze_rows(*increment_evidence(active_step))
    assert not result['integrity_checks_passed'], result['issues']
    assert not result['measurement_checks_passed']
    assert result['native_acceptance'] == 'not_accepted'


def test_actual_controller_keeps_existing_ordinary_plan_before_kit_selection(tmp_path):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    funding = deepcopy(loop.memory.solid_funding)
    step = Step('factory_craft', 'inventory', 'iron-gear-wheel',
                backend.state.inventory.get('iron-gear-wheel', 0) + 1,
                costs={'iron-plate': 2},
                parameters={'recipe': 'iron-gear-wheel', 'batches': 1})
    plan = Plan('ordinary-production', 'rocket_launch', 'Continue ordinary production', (step,))
    loop.memory.active_plan = asdict(plan)
    loop.memory.active_goal = plan.goal
    loop.memory.step_index = 0
    loop._save()
    initial = asdict(loop.memory)
    calls = len(backend.calls)
    def forbidden_recompile(snapshot):
        raise AssertionError('An active ordinary plan cannot enter candidate selection')
    loop._work_candidates = forbidden_recompile
    record = loop.step()
    final = asdict(loop.memory)
    assert record['action'] == 'factory_craft' and record['verified']
    assert record['decision'] is None
    assert len(backend.calls) == calls + 1
    assert final['solid_funding'] == funding
    assert not funding_history_issues(initial, [record], final)



def ordinary_window(tmp_path, mode='dispatch', count=1):
    from types import MethodType
    from jev_factorio.controller import HierarchicalLoop

    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    start = backend.state.inventory.get('iron-gear-wheel', 0)
    goal = 'stockpile_fuel' if mode == 'goal' else 'rocket_launch'
    batches = 200 if mode == 'failure' else 1
    steps = tuple(Step('factory_craft', 'inventory', 'iron-gear-wheel', start + batches + i,
                       costs={'iron-plate': 2 * batches},
                       parameters={'recipe': 'iron-gear-wheel', 'batches': batches})
                  for i in range(count))
    plan = Plan('ordinary-production', goal, 'Retain actual ordinary work', steps)
    loop.memory.active_plan = asdict(plan)
    loop.memory.active_goal = goal
    loop.memory.step_index = 0
    if mode == 'goal':
        loop._refresh_goals = MethodType(HierarchicalLoop._refresh_goals, loop)
    if mode == 'already':
        backend.state.inventory['iron-gear-wheel'] = start + count
    loop._save()
    if mode == 'pending':
        backend.lose_kit_ack = True
        pending = loop.step()
        assert not pending['verified'] and loop.memory.pending is not None
        backend.lose_kit_ack = False
        # Reconstruct from the durable checkpoint, not the live mixin fields.
        from solid_routes_fixtures import INTENTS
        loop = type(loop)(backend, target='rocket_launch', policy='deterministic',
            factory_scheduling='ready-work', tick_seconds=0, checkpoint=str(backend.checkpoint),
            resume_controller=True, solid_intents=INTENTS, solid_science_policy=True)
        loop._observe()
    initial = asdict(loop.memory)
    records = [loop.step()]
    if mode not in ('goal',):
        if mode == 'failure':
            assert not records[0]['verified'] and loop.memory.active_plan is None
        else:
            assert records[0]['verified'] and records[0]['decision'] is None
        for _ in range(count - 1):
            records.append(loop.step())
            assert records[-1]['verified'] and records[-1]['decision'] is None
        assert loop.memory.active_plan is None
        records.append(loop.step())
    assert records[-1]['decision'] is not None
    assert records[-1]['solid_funding']['actions'] == initial['solid_funding']['actions'] + 1
    return initial, records, asdict(loop.memory)


@pytest.mark.parametrize('mode,count', [('dispatch', 1), ('dispatch', 2),
    ('already', 1), ('failure', 1), ('goal', 1), ('pending', 1)])
def test_real_ordinary_release_allows_later_kit(mode, count, tmp_path):
    initial, records, final = ordinary_window(tmp_path, mode, count)
    assert not funding_history_issues(initial, records, final)


@pytest.mark.parametrize('change', ['step_hash', 'plan_id', 'step_index', 'verified',
    'finished_tick', 'process_id', 'missing_event', 'wrong_event', 'effect',
    'dispatch', 'invented_goal', 'goal_after_commit'])
def test_unproven_ordinary_release_cannot_enable_next_kit(change, tmp_path):
    initial, records, final = ordinary_window(tmp_path)
    first = records[0]
    outcome = next(v for v in first['attempt_outcomes'] if v['plan_id'] == 'ordinary-production')
    if change == 'step_hash': outcome['step_sha256'] = 'f' * 64
    elif change == 'plan_id': outcome['plan_id'] = 'unrelated-plan'
    elif change == 'step_index': outcome['step_index'] = 1
    elif change == 'verified': first['verified'] = False
    elif change == 'finished_tick': outcome['finished_tick'] += 1
    elif change == 'process_id': outcome['process_id'] = 'f' * 32
    elif change == 'effect': first['after_state']['inventory']['iron-gear-wheel'] = 0
    elif change == 'dispatch': outcome['dispatch_phases']['dispatch']['status'] = 'started'
    elif change in ('missing_event', 'wrong_event'):
        for record in records:
            for event in record['history']:
                if event.get('kind') == 'step_verified' and event.get('plan') == 'ordinary-production':
                    event['kind' if change == 'missing_event' else 'plan'] = 'unrelated'
        final['history'] = deepcopy(records[-1]['history'])
    else:
        # A completion label cannot support a new goal without its actual fact.
        first['verified'] = False
        tick = first['state']['tick']
        extras = [dict(kind='goal_completed', goal='rocket_launch', tick=tick,
                       world_kind=first['state']['world_kind']),
                  dict(kind='goal_activated', goal='stockpile_fuel', tick=tick)]
        for record in records:
            if change == 'invented_goal': record['history'] = extras + record['history']
            else: record['history'].extend(extras)
        final['history'] = deepcopy(records[-1]['history'])
    issues = funding_history_issues(initial, records, final)
    assert issues, change
    assert issues


@pytest.mark.parametrize('change', ['missing_budget', 'false_precondition', 'wrong_identity'])
def test_bare_failure_label_does_not_release_ordinary_plan(change, tmp_path):
    initial, records, final = ordinary_window(tmp_path, 'failure')
    if change == 'missing_budget':
        for record in records: record['failure_budgets'].pop('ordinary-production')
        final['failures'].pop('ordinary-production')
    elif change == 'false_precondition':
        records[0]['after_state']['inventory']['iron-plate'] = 10000
    else:
        for record in records:
            for event in record['history']:
                if event.get('kind') == 'plan_failed': event['plan'] = 'foreign-plan'
        final['history'] = deepcopy(records[-1]['history'])
    assert funding_history_issues(initial, records, final)


@pytest.mark.parametrize('index', [-1, True, 99])
def test_malformed_retained_step_index_fails_closed(index):
    records, _, initial, final = increment_evidence(0)
    initial['step_index'] = index
    assert funding_history_issues(initial, records, final)



def test_other_plan_verification_cannot_clear_retained_effect(tmp_path):
    initial, records, final = ordinary_window(tmp_path)
    first = records[0]
    first['action'] = 'verify'
    for value in first['attempt_outcomes']:
        if value['plan_id'] == 'ordinary-production': value['plan_id'] = 'unrelated-plan'
    for record in records:
        for event in record['history']:
            if event.get('kind') == 'step_verified' and event.get('plan') == 'ordinary-production':
                event['kind'] = 'unrelated'
    final['history'] = deepcopy(records[-1]['history'])
    assert funding_history_issues(initial, records, final)


def test_one_verified_step_does_not_complete_two_step_ordinary_plan(tmp_path):
    initial, records, final = ordinary_window(tmp_path, count=2)
    records.pop(1)
    assert funding_history_issues(initial, records, final)


@pytest.mark.parametrize('change', ['goal', 'boolean_completion'])
def test_goal_release_requires_consistent_record_fields(change, tmp_path):
    initial, records, final = ordinary_window(tmp_path, 'goal')
    if change == 'goal':
        records[0]['goal'] = 'stockpile_fuel'
    else:
        records[0]['completed_goals']['stockpile_fuel'] = False
    assert funding_history_issues(initial, records, final)


def test_step_verification_tick_requires_integer_schema(tmp_path):
    initial, records, final = ordinary_window(tmp_path)
    for record in records:
        for event in record['history']:
            if event.get('kind') == 'step_verified' and event.get('plan') == 'ordinary-production':
                event['tick'] = float(event['tick'])
    final['history'] = deepcopy(records[-1]['history'])
    issues = funding_history_issues(initial, records, final)
    assert issues


def test_continuity_analysis_does_not_mutate_input_evidence(tmp_path):
    data = ordinary_window(tmp_path, 'pending')
    saved = deepcopy(data)
    assert not funding_history_issues(*data)
    assert data == saved
