"""Downstream cost/demand and actual controller admission; synthetic, no native game."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from jev_factorio import solid_routes as contract
from jev_factorio.planning import solid_investment as policy
from jev_factorio.planning.decision_support import scheduling_context
from jev_factorio.skills import Plan, Step
from jev_factorio.solid_controller import solid_loop_type
from jev_factorio.telemetry import make_attempt, utc_now
from solid_routes_fixtures import fixture, row, build, full, INTENTS, SOURCE, TARGET, ROUTE
from test_factory import recipe
from test_solid_route_integration import Backend, FoundationScenario, native_catalog


def catalog():
    result = native_catalog()
    result.recipes.update({
        'copper-plate': recipe('copper-plate', {'copper-ore': 1}, 'smelting'),
        'copper-cable': recipe('copper-cable', {'copper-plate': 1}),
        'electronic-circuit': recipe('electronic-circuit', {'iron-plate': 1, 'copper-cable': 3}),
        'transport-belt': recipe('transport-belt', {'iron-plate': 1, 'iron-gear-wheel': 1}),
        'inserter': recipe('inserter', {'iron-plate': 1, 'iron-gear-wheel': 1, 'electronic-circuit': 1}),
    })
    result.technologies['study'] = dict(enabled=True, effects=[], prerequisites=[],
        ingredients=[{'name': 'automation-science-pack', 'amount': 1}], count=120, energy_ticks=60)
    result.technologies['rocket-silo'] = {**deepcopy(result.technologies['study']), 'prerequisites': ['study']}
    return result


def scenario():
    state = fixture()
    state.factory.update(research='study', research_progress=0)
    state.factory['entities'][SOURCE]['output']['iron-gear-wheel'] = 120
    state.factory['entities']['utility:lab'] = dict(name='lab', unit_number=900, energy=100,
                                                  input={}, position={'x': 9, 'y': 9})
    state.factory['entities'][TARGET]['input']['copper-plate'] = 120
    return state, catalog()


def service_history(state, ticks=5000):
    result = []
    cursor = 300
    for endpoint, action in ((row(state)['source'], 'factory_extract'), (row(state)['target'], 'factory_insert')):
        for index in range(3):
            parameters = {'role': endpoint['role'], 'item': row(state)['item'], 'quantity': 20,
                          'receipt': f"{cursor}:{action}:{endpoint['role']}:{row(state)['item']}"}
            plan = Plan(f"factory:{action}:{endpoint['role']}", 'rocket_launch', 'Fixture service',
                        (Step(action, 'transfer', parameters=parameters),))
            attempt = make_attempt(state.session_id, 'rocket_launch', plan.to_dict(), 0,
                                   {'started_tick': cursor}, process_id='a' * 32,
                                   unit_number=endpoint['unit_number'])
            result.append({**attempt, 'outcome': 'verified', 'finished_tick': cursor + ticks,
                           'finished_at_utc': utc_now(), 'latency_seconds': None})
            cursor += ticks + 1
    state.tick = cursor + 10
    state.factory['solid_routes']['tick'] = state.tick
    return result


def offers(state, data, history=(), **options):
    return policy.candidates(state, data, 'rocket_launch', outcomes=history, **options)


def test_current_stocked_upstream_bill_is_not_lost_from_demand():
    state, data = scenario()
    demand, reason = policy.requirements(state, data)
    assert reason == 'current_research_recipe_bill'
    assert demand['automation-science-pack']['iron-gear-wheel'] == 120
    assert not policy.offer_value(row(state), state, data, demand)['eligible']
    history = service_history(state)
    value = policy.offer_value(row(state), state, data, demand, history)
    assert value['eligible'] and value['service_basis'] == 'verified_attempt_game_ticks'
    assert value['valued_units'] == 120 and value['flow_proven'] is False
    assert not contract.flow_complete(ROUTE, row(state)['layout'], state)


@pytest.mark.parametrize('change', ['complete', 'unknown', 'nan', 'trigger', 'unsupported_pack', 'future_recipe', 'covered'])
def test_unknown_or_changed_research_never_creates_speculative_investment(change):
    state, data = scenario(); history = service_history(state)
    if change == 'complete': state.factory['research_progress'] = 1
    elif change == 'unknown': state.factory.pop('research_progress')
    elif change == 'nan': state.factory['research_progress'] = float('nan')
    elif change == 'trigger': data.technologies['study']['trigger'] = 'craft-item'
    elif change == 'unsupported_pack': data.technologies['study']['ingredients'][0]['name'] = 'chemical-science-pack'
    elif change == 'future_recipe': state.factory['research'] = 'not-observed'
    else: state.factory['entities']['utility:lab']['input']['automation-science-pack'] = 120
    assert offers(state, data, history)[0] == []


@pytest.mark.parametrize('change', ['empty', 'mixed', 'source_power', 'target_power', 'target_stock', 'locked', 'kit', 'alias', 'recipe'])
def test_supply_power_identity_and_reservation_boundaries(change):
    state, data = scenario(); history = service_history(state); options = {}
    entities = state.factory['entities']
    if change == 'empty': entities[SOURCE]['output'].clear()
    elif change == 'mixed': entities[SOURCE]['output']['coal'] = 1
    elif change == 'source_power': entities[SOURCE]['energy'] = 0
    elif change == 'target_power': entities[TARGET]['energy'] = 0
    elif change == 'target_stock': entities[TARGET]['input']['iron-gear-wheel'] = 120
    elif change == 'locked': options['reserved'] = {'inserter': 1}
    elif change == 'kit': state.inventory['inserter'] = 1
    elif change == 'alias': entities['alias'] = deepcopy(entities[SOURCE])
    else: entities[TARGET]['recipe'] = 'iron-gear-wheel'
    assert offers(state, data, history, **options)[0] == []


def test_actual_source_stock_caps_valued_repeated_hauling():
    state, data = scenario(); history = service_history(state)
    state.factory['entities'][SOURCE]['output']['iron-gear-wheel'] = 1
    value = policy.offer_value(row(state), state, data, policy.requirements(state, data)[0], history)
    assert value['demand_units'] == 120 and value['valued_units'] == 1
    assert value['manual_trips_estimate'] == 1


@pytest.mark.parametrize('change', ['duplicate', 'receipt', 'wrong_unit', 'future', 'old', 'legacy', 'malformed'])
def test_invalid_service_history_is_not_measured_cost(change):
    state, _ = scenario(); history = service_history(state)
    if change == 'duplicate': history[2] = deepcopy(history[1])
    elif change == 'receipt': history[2]['receipt'] = history[1]['receipt']
    elif change == 'wrong_unit': history[2]['expected_unit_number'] += 1
    elif change == 'future': history[2]['finished_tick'] = state.tick + 1
    elif change == 'old': state.tick += policy.MAX_SERVICE_TICKS + 1
    elif change == 'legacy': history[2]['origin'] = 'legacy'
    else: history[2] = {'arbitrary': 'not attempt evidence'}
    _, basis, counts = policy.service_cost(row(state), history, state.tick)
    assert basis == 'estimated_round_trip_and_service'
    assert min(counts.values()) < 3


@pytest.mark.parametrize('change', ['stock', 'power', 'research', 'source', 'kit', 'marker', 'id'])
def test_selected_investment_is_rechecked_before_mutation(change):
    state, data = scenario(); history = service_history(state)
    selected = offers(state, data, history)[0][0]
    state.tick += 10; state.factory['solid_routes']['tick'] = state.tick
    assert policy.fresh_permission(selected, selected.steps[0], state, data, outcomes=history)
    if change == 'stock': state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 120
    elif change == 'power': state.factory['entities'][TARGET]['energy'] = 0
    elif change == 'research': state.factory['research_progress'] = 1
    elif change == 'source': state.factory['entities'][SOURCE]['output'].clear()
    elif change == 'kit': state.inventory['inserter'] = 0
    elif change == 'marker': selected.materials[policy.MARKER]['layout'] = 'different'
    else: selected = replace(selected, id=selected.id + ':retry')
    assert not policy.fresh_permission(selected, selected.steps[0], state, data, outcomes=history)


def test_paid_prefix_continues_without_new_project_or_repricing():
    state, data = scenario(); history = service_history(state)
    selected = offers(state, data, history)[0][0]
    build(state, selected.steps[0].parameters)
    state.factory['research_progress'] = 1
    later, diagnostics = offers(state, data, history)
    assert len(later) == 1 and later[0].id.rsplit(':', 1)[0] != ''
    assert diagnostics['routes'][ROUTE]['reason'] == 'preserve_paid_commitment'
    assert later[0].steps[0].parameters['part'] == 'belt:2'
    assert policy.fresh_permission(later[0], later[0].steps[0], state, data, outcomes=history)


def test_existing_project_crafting_or_other_capital_defers_new_route():
    state, data = scenario(); history = service_history(state)
    assert offers(state, data, history, capital_active=True)[0] == []
    state.factory['crafting_queue'] = 1
    assert offers(state, data, history)[0] == []


def make_loop(tmp_path, *, resume=False, enabled=True, research_log=None):
    backend = Backend(); backend.state, data = scenario()
    backend.enable_factory = lambda: data
    history = service_history(backend.state)
    backend.checkpoint = tmp_path / 'policy-checkpoint.json'
    kind = solid_loop_type(FoundationScenario)
    loop = kind(backend, target='rocket_launch', policy='deterministic',
                factory_scheduling='ready-work', tick_seconds=0,
                checkpoint=str(backend.checkpoint), resume_controller=resume,
                solid_intents=INTENTS, solid_science_policy=enabled, research_log=research_log)
    if not resume:
        loop.memory = loop.memory_type(backend.state.session_id, 'rocket_launch', active_goal='rocket_launch',
            completed_goals={'stockpile_fuel': 0, 'bootstrap_mining': 0}, last_tick=backend.state.tick)
        loop.memory.attempt_outcomes = history
    return loop, backend


def test_controller_selects_and_pays_for_policy_offer(tmp_path):
    loop, backend = make_loop(tmp_path)
    result = loop.step()
    assert result['verified'] and result['action'] == contract.COMMAND
    assert len(backend.calls) == 1 and len(loop.memory.solid_commitments[ROUTE]['parts']) == 1
    assert result['solid_science_policy'] is True
    assert result['acceptance_configuration']['solid_science_policy'] is True
    assert loop.memory.solid_science_policy is True
    assert loop.memory.failures == {}


def test_fresh_changed_stock_stops_actual_controller_dispatch(tmp_path):
    loop, backend = make_loop(tmp_path)
    def change():
        if backend.observations == 2:
            backend.state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = 120
    backend.before_observe = change
    result = loop.step()
    assert not result['verified'] and backend.calls == [] and loop.memory.pending is None


def test_ranking_marker_is_decision_local_and_ready_science_wins(tmp_path):
    loop, backend = make_loop(tmp_path)
    state = loop._observe()
    plans, _ = loop._compile_candidates(state)
    assert plans and policy.ranking_marker(plans[0], state)
    delivery = Plan('ready-science', 'rocket_launch', 'Ready science',
        (Step('factory_insert', 'transfer', parameters={'role': 'utility:lab',
         'item': 'automation-science-pack', 'quantity': 20, 'receipt': 'science'}),))
    context = scheduling_context(state, loop.catalog, [plans[0], delivery], 'rocket_launch')
    assert context['candidate_evidence'][plans[0].id]['urgency'] == 1
    assert context['deterministic_ranking'][0] == delivery.id
    forged = deepcopy(plans[0]); forged.materials[policy.MARKER]['manual_game_ticks_estimate'] *= 2
    assert not policy.ranking_marker(forged, state)
    fresh = deepcopy(state); fresh.tick += 1
    assert not policy.ranking_marker(plans[0], fresh)


def test_immutable_policy_cannot_be_toggled_on_resume(tmp_path):
    loop, backend = make_loop(tmp_path); loop.step()
    kind = type(loop)
    with pytest.raises(ValueError, match='silently'):
        kind(backend, solid_intents=INTENTS, solid_science_policy=False, target='rocket_launch',
            policy='deterministic', factory_scheduling='ready-work', checkpoint=str(backend.checkpoint),
            resume_controller=True)
    restored = kind.memory_type.load(backend.checkpoint, backend.state.session_id, 'rocket_launch')
    assert restored.solid_science_policy is True


def test_legacy_explicit_checkpoint_is_not_silently_opted_into_policy(tmp_path):
    loop, backend = make_loop(tmp_path, enabled=False); loop.step()
    data = json.loads(backend.checkpoint.read_text()); data.pop('solid_science_policy')
    backend.checkpoint.write_text(json.dumps(data))
    restored = type(loop).memory_type.load(backend.checkpoint, backend.state.session_id, 'rocket_launch')
    assert restored.solid_science_policy is False
    with pytest.raises(ValueError, match='silently'):
        type(loop)(backend, solid_intents=INTENTS, solid_science_policy=True, target='rocket_launch',
            policy='deterministic', factory_scheduling='ready-work', checkpoint=str(backend.checkpoint),
            resume_controller=True)


def test_manifest_must_declare_exact_policy(tmp_path):
    from jev_factorio.research_log import ResearchLog, RunConfiguration
    cfg = RunConfiguration(backend='mock', controller='hierarchical', policy='deterministic',
                           factory_scheduling='ready-work', solid_routes=True)
    with ResearchLog(tmp_path / 'run', cfg, environ={}) as sink:
        with pytest.raises(ValueError, match='manifest'):
            make_loop(tmp_path, research_log=sink)


def test_policy_run_has_valid_research_evidence_chain(tmp_path):
    from jev_factorio.research_log import ResearchLog, RunConfiguration, verify_run
    cfg = RunConfiguration(backend='mock', controller='hierarchical', policy='deterministic',
        target='rocket_launch', factory_scheduling='ready-work', solid_routes=True,
        solid_science_policy=True, checkpoint_enabled=True)
    with ResearchLog(tmp_path / 'run', cfg, environ={}) as sink:
        loop, backend = make_loop(tmp_path, research_log=sink)
        assert loop.step()['verified']
    assert verify_run(tmp_path / 'run')['complete']


@pytest.mark.parametrize('change', ['wrong_item', 'overlap', 'missing_lab', 'unpowered_lab'])
def test_service_evidence_and_research_delivery_are_bound(change):
    state, data = scenario(); history = service_history(state)
    if change == 'wrong_item':
        history[2]['receipt'] = history[2]['receipt'].replace('iron-gear-wheel', 'copper-plate')
    elif change == 'overlap':
        history[2]['started_tick'] = history[1]['started_tick']
        history[2]['finished_tick'] = history[1]['finished_tick']
        history[2]['receipt'] = str(history[2]['started_tick']) + ':factory_extract:' + SOURCE + ':iron-gear-wheel'
    elif change == 'missing_lab':
        state.factory['entities'].pop('utility:lab')
    else:
        state.factory['entities']['utility:lab']['energy'] = 0
    assert offers(state, data, history)[0] == []


def test_policy_native_prepared_recovery_survives_advancing_tick(tmp_path):
    loop, backend = make_loop(tmp_path)
    backend.prepared_once = True
    assert not loop.step()['verified']
    backend.state.tick += 60
    backend.state.factory['solid_routes']['tick'] = backend.state.tick
    record = loop.step()
    assert record['verified'] and len(backend.calls) == 2
    assert backend.calls[0] == backend.calls[1]
    assert len(row(backend.state)['parts']) == 1 and loop.memory.pending is None


def test_policy_unknown_manifest_configuration_rejected(tmp_path):
    from jev_factorio.research_log import ResearchLog, RunConfiguration
    cfg = RunConfiguration(backend='mock', controller='hierarchical', policy='deterministic',
                           solid_science_policy=True)
    with pytest.raises(ValueError, match='requires solid'):
        ResearchLog(tmp_path / 'invalid-run', cfg, environ={})


@pytest.mark.parametrize('fuel', [2, 1, 0])
@pytest.mark.parametrize('missing_kit', [False, True])
def test_real_composed_frontier_keeps_ready_science_with_justified_policy(tmp_path, fuel, missing_kit):
    from test_maintenance_progress import progress_scenario
    from test_input_route_integration import RouteLoop
    backend, data = progress_scenario(fuel=fuel)
    extra, extra_data = scenario()
    state = backend.state
    state.factory['solid_routes'] = deepcopy(extra.factory['solid_routes'])
    for role in (SOURCE, TARGET):
        state.factory['entities'][role] = deepcopy(extra.factory['entities'][role])
    data.recipes.update(extra_data.recipes)
    data.machines.update(extra_data.machines)
    data.technologies['study']['ingredients'] = [
        {'name': 'automation-science-pack', 'amount': 1},
        {'name': 'logistic-science-pack', 'amount': 1}]
    data.technologies['study']['count'] = 120
    history = service_history(state)
    for key in ('solid_routes', 'input_routes', 'output_buffers'):
        if key in state.factory:
            state.factory[key]['session_id'] = state.session_id
            state.factory[key]['tick'] = state.tick
    state.inventory.update(inserter=2, **{'transport-belt': 22})
    if missing_kit:
        state.inventory.update(inserter=0, **{'transport-belt': 0, 'iron-plate': 200, 'copper-plate': 100})
    backend.solid_routes_supported = True
    kind = solid_loop_type(RouteLoop)
    loop = kind(backend, target='rocket_launch', policy='deterministic',
                factory_scheduling='ready-work', tick_seconds=0,
                checkpoint=str(tmp_path / 'composed.json'), solid_intents=INTENTS,
                solid_science_policy=True)
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch',
        completed_goals={'stockpile_fuel': 0, 'bootstrap_mining': 0}, last_tick=state.tick)
    loop.memory.attempt_outcomes = history
    plans, blocker = loop._compile_candidates(state)
    deliveries = [plan for plan in plans if plan.steps[0].action == 'factory_insert'
                  and plan.steps[0].parameters['role'] == 'utility:lab'
                  and plan.steps[0].parameters['item'] == 'logistic-science-pack']
    builders = [plan for plan in plans if ((plan.materials or {}).get(policy.MARKER, {}).get('stage') == 'kit'
                if missing_kit else plan.steps[0].action == contract.COMMAND)]
    assert deliveries, blocker
    assert builders, loop._solid_policy_evidence
    evidence = scheduling_context(state, data, plans, 'rocket_launch')['candidate_evidence']
    assert evidence[deliveries[0].id]['urgency'] > evidence[builders[0].id]['urgency']
    assert backend.calls == []
