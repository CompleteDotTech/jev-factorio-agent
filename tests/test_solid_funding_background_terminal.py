"""Real background composition and owned non-success attempts in funding replay."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.solid_controller import solid_loop_type
from jev_factorio.solid_funding_evidence import funding_history_issues
from jev_factorio.skills import Plan, Step
from jev_factorio.operational_safety import StoragePressure, MaintenanceAdmissionClosed
from solid_routes_fixtures import fixture, INTENTS
from test_background_work import ReceiptBackend, ScenarioLoop
from test_solid_kit_acquisition import kit_loop


def background_loop(tmp_path):
    backend = ReceiptBackend()
    backend.solid_routes_supported = True
    route_state = fixture()
    backend.state.factory['entities'].update(route_state.factory['entities'])
    backend.state.factory['solid_routes'] = deepcopy(route_state.factory['solid_routes'])
    original = backend.observe
    def observe():
        backend.state.factory['solid_routes'].update(session_id=backend.state.session_id, tick=backend.state.tick)
        return original()
    backend.observe = observe
    kind = solid_loop_type(ScenarioLoop)
    loop = kind(backend, policy='deterministic', factory_scheduling='ready-work',
        target='automation_science', checkpoint=str(tmp_path / 'background.json'), tick_seconds=0,
        solid_intents=INTENTS, solid_science_policy=True)
    loop.memory = loop.memory_type(backend.state.session_id, loop.target, active_goal=loop.target,
        completed_goals={goal: 0 for goal in loop.order[:-1]}, last_tick=backend.state.tick)
    loop._compile_candidates = lambda snapshot: ScenarioLoop._compile_candidates(loop, snapshot)
    loop._observe()
    return loop, backend


@pytest.mark.parametrize('completion', [False, True])
def test_retained_background_work_replays_independent_work_and_completion(tmp_path, completion):
    loop, backend = background_loop(tmp_path)
    first = loop.step()
    assert first['background_job'] and first['action'] == 'factory_craft_job'
    initial = asdict(loop.memory)
    if completion: backend.complete()
    record = loop.step(); final = asdict(loop.memory)
    assert record['background_job'] is None if completion else record['action'] == 'factory_gather'
    assert not funding_history_issues(initial, [record], final)
    if completion:
        record['attempt_outcomes'][-1]['receipt'] = 'invented'
        final['attempt_outcomes'] = deepcopy(record['attempt_outcomes'])
        assert funding_history_issues(initial, [record], final)


def test_full_background_admission_work_completion_window(tmp_path):
    loop, backend = background_loop(tmp_path)
    initial = asdict(loop.memory)
    records = [loop.step(), loop.step()]
    backend.complete(); records.append(loop.step())
    assert not funding_history_issues(initial, records, asdict(loop.memory))


@pytest.mark.parametrize('error', [StoragePressure, MaintenanceAdmissionClosed])
@pytest.mark.parametrize('boundary', ['backend', 'guard'])
def test_explicit_preflight_outcome_preserves_the_retained_plan(tmp_path, error, boundary):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    plan = Plan('ordinary-preflight', 'rocket_launch', 'Ordinary production', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', 999,
             costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    loop._compile_candidates = lambda snapshot: ([plan], '')
    def reject(*args): raise error()
    if boundary == 'backend': backend.execute = reject
    else: loop._trace.admission_check = reject
    initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert not record['verified'] and record['attempt_outcomes'][-1]['outcome'].endswith('preflight_rejected')
    from jev_factorio.telemetry import validate_attempt
    validate_attempt(record['attempt_outcomes'][-1], finished=True)
    assert final['active_plan']['id'] == plan.id
    assert not funding_history_issues(initial, [record], final)
    record['history'] = [event for event in record['history'] if not event['kind'].endswith('preflight_rejected')]
    final['history'] = deepcopy(record['history'])
    assert funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('outcome', ['wait_expired', 'wait_replanned'])
def test_retained_nonmutating_wait_can_close_without_verification(tmp_path, outcome):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    plan = Plan('ordinary-wait', 'rocket_launch', 'Wait for research', (
        Step('factory_wait', 'research_progress', threshold=1),))
    loop._compile_candidates = lambda snapshot: ([plan], '')
    backend.execute = lambda *args: 'waiting'
    backend.act = lambda *args: 'idle'
    loop.max_pending_polls = 1
    loop.step()
    assert loop.memory.pending
    if outcome == 'wait_replanned':
        backend.state.factory['entities']['utility:boiler'] = {'fuel': {'coal': 0}}
    initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['attempt_outcomes'][-1]['outcome'] == outcome
    assert not funding_history_issues(initial, [record], final)
    record['attempt_outcomes'][-1]['id'] = 'f' * 32
    final['attempt_outcomes'] = deepcopy(record['attempt_outcomes'])
    assert funding_history_issues(initial, [record], final)


def test_fresh_ordinary_plan_cannot_ignore_its_exhausted_budget(tmp_path):
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    plan = Plan('ordinary-exhausted', 'rocket_launch', 'Ordinary production', (
        Step('factory_craft', 'inventory', 'iron-gear-wheel', backend.state.inventory.get('iron-gear-wheel', 0) + 1,
             costs={'iron-plate': 2}, parameters={'recipe': 'iron-gear-wheel', 'batches': 1}),))
    loop._compile_candidates = lambda snapshot: ([plan], '')
    initial = asdict(loop.memory); record = loop.step(); final = asdict(loop.memory)
    assert record['verified'] and not funding_history_issues(initial, [record], final)
    initial['failures'][plan.id] = final['failures'][plan.id] = record['failure_budgets'][plan.id] = 2
    assert funding_history_issues(initial, [record], final)


def test_connection_preflight_closes_its_owned_attempt(tmp_path):
    from jev_factorio.backends.errors import ConnectionPreflightRejected
    from solid_routes_fixtures import SOURCE, TARGET
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    backend.state.inventory['pipe'] = 1
    plan = Plan('ordinary-connect', 'rocket_launch', 'Connection', (
        Step('factory_connect', 'connection', costs={'pipe': 1},
             parameters={'source': SOURCE, 'target': TARGET, 'kind': 'pipe', 'fluid': 'water'}),))
    loop._compile_candidates = lambda snapshot: ([plan], '')
    def reject(*args): raise ConnectionPreflightRejected('missing_fluid_port')
    backend.execute = reject
    initial = asdict(loop.memory); record = loop.step(); final = asdict(loop.memory)
    assert record['attempt_outcomes'][-1]['outcome'] == 'connection_preflight_rejected'
    assert not funding_history_issues(initial, [record], final)
    record['failure_budgets'][plan.id] = final['failures'][plan.id] = 0
    assert funding_history_issues(initial, [record], final)


def test_exhausted_project_can_release_then_fund_another_project(tmp_path):
    from uuid import uuid4
    from jev_factorio.planning import solid_funding
    from jev_factorio.telemetry import fingerprint
    from solid_routes_fixtures import row
    loop, backend = kit_loop(tmp_path)
    assert loop.step()['verified']
    old = deepcopy(loop.memory.solid_funding)
    other = deepcopy(row(backend.state))
    other.update(route='solid:15001:15002:iron-gear-wheel:input', layout='second-kit-layout')
    for name, unit in [('source', 15001), ('target', 15002)]:
        endpoint = other[name]
        entity = deepcopy(backend.state.factory['entities'][endpoint['role']])
        endpoint.update(role='second:' + name, unit_number=unit)
        endpoint['position']['y'] += 20
        for corner in endpoint['bounds'].values(): corner['y'] += 20
        entity.update(unit_number=unit, position=deepcopy(endpoint['position']))
        backend.state.factory['entities'][endpoint['role']] = entity
    for step in other['steps']: step['position']['y'] += 20
    backend.state.factory['solid_routes']['routes'][other['route']] = other
    backend.state.factory['solid_routes']['diagnostics'].append({'intent_index': 2, 'state': 'proposed', 'reason': 'ready_layout'})
    loop._solid_intents.append(solid_funding.intent(other))
    loop.memory.solid_intents = deepcopy(loop._solid_intents)
    samples = [deepcopy(value) for value in loop.memory.attempt_outcomes if value['action'] in {'factory_extract', 'factory_insert'}]
    for value in samples:
        endpoint = other['source'] if value['action'] == 'factory_extract' else other['target']
        params = {'role': endpoint['role'], 'item': other['item'], 'quantity': 20,
                  'receipt': f"{value['started_tick']}:{value['action']}:{endpoint['role']}:{other['item']}"}
        value.update(id=uuid4().hex, plan_id='factory:' + value['action'] + ':' + endpoint['role'],
            receipt=params['receipt'], expected_unit_number=endpoint['unit_number'],
            step_sha256=fingerprint(asdict(Step(value['action'], 'transfer', parameters=params))))
    loop.memory.attempt_outcomes.extend(samples)
    loop._observe()  # Declare the second catalog before capturing the baseline.
    loop.memory.solid_funding['actions'] = solid_funding.MAX_ACTIONS
    loop.memory.history = []  # A valid truncated initial history, without stale action counts.
    initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['verified'] and final['solid_funding']['route'] == other['route']
    assert any(event.get('reason') == 'kit_action_budget' for event in record['history'])
    assert not funding_history_issues(initial, [record], final)


def test_verify_only_kit_commit_cannot_overlap_a_background_job(tmp_path):
    from test_solid_funding_evidence import funded_evidence, event, plan_event, decision_for
    from jev_factorio.planning import solid_funding
    loop, _ = background_loop(tmp_path); loop.step()
    data = funded_evidence(); records, _, initial, final = data
    proof = deepcopy(initial['solid_funding']); proof['actions'] += 1
    record = records[3]; tick = record['state']['tick']
    commit = event('solid_kit_committed', proof, tick)
    step = Step(**commit['step'])
    record.update(action='verify', verified=True, history=[commit, plan_event(proof, tick)])
    decision_for(record, proof)
    record['after_state']['inventory'][step.item] = step.threshold
    for value in records[3:]:
        value['solid_funding'] = deepcopy(proof)
        value['history'] = deepcopy(record['history'])
    final['solid_funding'] = deepcopy(proof); final['history'] = deepcopy(record['history'])
    assert not funding_history_issues(initial, records, final)
    record['background_job'] = deepcopy(loop.memory.background_job)
    record['background_attempt'] = deepcopy(loop.memory.background_attempt)
    assert funding_history_issues(initial, records, final)


def test_background_completion_can_precede_a_new_job_in_the_same_iteration(tmp_path):
    loop, backend = background_loop(tmp_path)
    loop.step()
    initial = asdict(loop.memory)
    backend.complete()
    backend.state.inventory['iron-plate'] = 10
    loop._refresh_goals = lambda snapshot: None
    execute = backend.execute
    def execute_with_current_baseline(action, parameters):
        baseline = backend.state.inventory.get('automation-science-pack', 0)
        result = execute(action, parameters)
        if action == 'factory_craft_job': backend.state.factory['craft_job']['baseline']['automation-science-pack'] = baseline
        return result
    backend.execute = execute_with_current_baseline
    record = loop.step(); final = asdict(loop.memory)
    assert record['background_job'] and record['background_attempt']['id'] != initial['background_attempt']['id']
    assert any(event['kind'] == 'background_job_completed' for event in record['history'])
    assert not funding_history_issues(initial, [record], final)


def test_background_completion_cannot_leave_an_unowned_attempt(tmp_path):
    loop, backend = background_loop(tmp_path)
    loop.step(); initial = asdict(loop.memory)
    backend.complete(); record = loop.step(); final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    record['background_attempt'] = deepcopy(initial['background_attempt'])
    record['background_attempt']['id'] = 'f' * 32
    final['background_attempt'] = deepcopy(record['background_attempt'])
    assert funding_history_issues(initial, [record], final)
