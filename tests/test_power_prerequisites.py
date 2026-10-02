"""Planner regressions for the live empty-boiler deadlock; synthetic evidence."""
from copy import deepcopy
from importlib.resources import files
from types import SimpleNamespace

import pytest

from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.background_work import independent_candidates
from test_economic_production import economic_catalog, economic_state
from test_factory import recipe


def power_state():
    data, state = economic_catalog(), economic_state()
    data.recipes['steam-engine'] = recipe('steam-engine', {'iron-plate': 10})
    data.recipes['pipe'] = recipe('pipe', {'iron-plate': 1})
    data.recipes['small-electric-pole'] = recipe('small-electric-pole', {'wood': 1})
    data.machines['boiler'] = {'burner': True, 'electric': False, 'categories': {}}
    state.inventory = {'coal': 5, 'steam-engine': 1, 'pipe': 200, 'small-electric-pole': 200}
    state.factory['entities']['utility:boiler']['fuel'] = {}
    state.factory['entities']['utility:lab']['energy'] = 0
    # These tests isolate the power path. Keep the current study's bounded
    # science requirement supplied so a missing lab pack does not correctly
    # precede boiler service in the research planner.
    state.factory['entities']['utility:lab']['input'] = {'automation-science-pack': 1}
    return data, state


def test_missing_engine_precedes_paid_coal_and_survives_replanning():
    data, state = power_state()
    del state.factory['entities']['utility:engine']
    before = deepcopy(state)
    for planner_type in (FactoryPlanner, ReadyWorkPlanner):
        for _ in range(2):
            plan = planner_type(data, state, 'rocket_launch').plan()
            assert plan.steps[0].action == 'factory_place'
            assert plan.steps[0].parameters['role'] == 'utility:engine'
            assert plan.materials['utility_power_prerequisite']['consumer_role'] == 'utility:lab'
            assert plan.materials['utility_power_prerequisite']['research'] == 'study'
    assert state == before


@pytest.mark.parametrize('missing,source,target,kind,fluid', [
    ('water', 'utility:water', 'utility:boiler', 'pipe', 'water'),
    ('steam', 'utility:boiler', 'utility:engine', 'pipe', 'steam'),
    ('electricity', 'utility:engine', 'utility:lab', 'small-electric-pole', 'electricity'),
])
def test_each_missing_physical_connection_precedes_fuel(missing, source, target, kind, fluid):
    data, state = power_state()
    entity = state.factory['entities'][target]
    if missing == 'electricity':
        entity['electric_network_id'] = None
    else:
        entity['fluid_ports'] = [p for p in entity['fluid_ports'] if p['fluid'] != missing]
    step = FactoryPlanner(data, state, 'rocket_launch').plan().steps[0]
    assert step.action == 'factory_connect'
    assert step.parameters == {'source': source, 'target': target, 'kind': kind, 'fluid': fluid}


@pytest.mark.parametrize('current', [0, 1, 2, 3, 4])
def test_connected_boiler_requests_only_five_coal_operating_deficit(current):
    data, state = power_state()
    state.factory['entities']['utility:boiler']['fuel'] = {'coal': current}
    plan = FactoryPlanner(data, state, 'rocket_launch').plan()
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters['role'] == 'utility:boiler'
    assert plan.steps[0].parameters['quantity'] == 5 - current
    assert plan.steps[0].costs == {'coal': 5 - current}
    assert plan.materials['utility_power_prerequisite']['research'] == 'study'


def test_empty_unconnected_boiler_does_not_block_non_power_goal():
    data, state = power_state()
    del state.factory['entities']['utility:engine']
    state.inventory['iron-plate'] = 10
    assert FactoryPlanner(data, state, 'iron_smelting').plan() is None


def test_empty_boiler_does_not_hide_capability_research_dependency():
    data, state = power_state()
    state.researched = []
    state.factory['research'] = ''
    del state.factory['entities']['utility:engine']
    plan = ReadyWorkPlanner(data, state, 'rocket_launch').plan()
    assert plan.steps[0].action == 'factory_place'
    assert plan.steps[0].parameters['role'] == 'utility:engine'
    assert plan.materials['utility_power_prerequisite']['research'] == 'automation'
    assert plan.materials['economics']['technology'] == 'automation'


def test_native_catalog_exports_boiler_burner_without_crafting_api():
    LuaRuntime = pytest.importorskip('lupa').LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute('''
        script = {active_mods={base='2.0.77'}}
        prototypes = {item={}, recipe={}, entity={
            character={crafting_categories={crafting=true}},
            boiler={burner_prototype={}, electric_energy_source_prototype=nil},
        }}
        storage = {agent_characters={{force={recipes={}, technologies={}}}}}
        rcon = {print=function(value) captured=value end}
        helpers = {table_to_json=function(value) return value end}
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/catalog.lua').read_text())
    boiler = lua.globals().captured.machines.boiler
    assert boiler.burner is True
    assert boiler.electric is False
    assert boiler.speed == 0


@pytest.mark.parametrize('connected', [False, True])
def test_background_boiler_maintenance_requires_existing_connected_plant(connected):
    data, state = power_state()
    if not connected:
        del state.factory['entities']['utility:engine']
    # Isolate the maintenance shortcut from normal lookahead candidates.
    class MaintenanceOnly(ReadyWorkPlanner):
        def candidates(self):
            return []
    job = SimpleNamespace(outputs={}, baseline={}, permits=lambda step: True)
    plans = independent_candidates('steam_power', state, data, job,
                                  planner_type=MaintenanceOnly)
    assert len(plans) == int(connected)
    if connected:
        assert plans[0].steps[0].action == 'factory_insert'
        assert plans[0].steps[0].parameters['role'] == 'utility:boiler'
        assert plans[0].materials['utility_power_prerequisite']['consumer_role'] == 'utility:lab'


def test_power_model_guidance_requires_action_bound_evidence():
    from jev_factorio.judgments import _qualified_utility_power_dependency, question_batch
    from jev_factorio.planning.decision_support import candidate_evidence
    from test_utility_power_prerequisite_evidence import fixture

    data, state, goal = fixture(fuel=1, coal=5)
    plan = FactoryPlanner(data, state, goal).plan()
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert row['work_scope'] == 'immediate'
    assert _qualified_utility_power_dependency(plan, row, state.tick)
    _, questions, selected = question_batch({
        'active_goal': goal, 'facts': {'tick': state.tick},
        'candidate_evidence': {plan.id: row},
    }, [plan])
    assert selected == [plan]
    assert 'current consumer\'s native power prerequisite' in questions[plan.id + '/benefit']['instructions']
    for field, value in (
        ('consumer_unit', True), ('next_action', 'factory_place'),
        ('planner_path', ['technology:unrelated']), ('boiler_coal_deficit_to_five', 50),
        ('planned_native_receipt', 'other'), ('transfer_receipt_observed_now', True),
        ('consumer_demand', {}), ('connections_current', {}),
        ('observed_tick', state.tick - 1),
    ):
        changed = deepcopy(row)
        changed['utility_power_prerequisite_start_evidence'][field] = value
        assert not _qualified_utility_power_dependency(plan, changed, state.tick)
    assert not _qualified_utility_power_dependency(plan, {
        'utility_power_prerequisite_start_evidence': {'observed_tick': state.tick},
    }, state.tick)


def test_power_fuel_receipt_clocks_do_not_retrigger_rejected_decisions():
    from jev_factorio.blocked_persistence import decision_input_sha256
    from jev_factorio.planning.decision_support import candidate_evidence
    from test_utility_power_prerequisite_evidence import fixture, nativeize

    data, state, goal = fixture(fuel=1, coal=5)

    def fingerprint(tick, *, actual_receipt=None):
        state.tick = tick
        nativeize(state, data)
        plan = FactoryPlanner(data, state, goal).plan()
        context = {'facts': {'tick': tick, 'inventory': state.inventory},
                   'candidate_evidence': candidate_evidence(state, data, [plan])}
        if actual_receipt is not None:
            context['facts']['factory'] = {'receipts': {'paid': {'native_receipt': actual_receipt}}}
        return decision_input_sha256(context, [plan.to_dict()],
            session_id=state.session_id, source_revision={'commit': '2' * 40, 'source_sha256': 'c' * 64},
            target=goal, policy='jev', confidence_floor=.45, current_tick=tick)

    baseline = fingerprint(10)
    assert fingerprint(30010) == baseline
    state.factory['entities']['utility:boiler']['fuel']['coal'] = 0
    assert fingerprint(30010) != baseline
    assert fingerprint(30010, actual_receipt='10:factory_insert:utility:boiler:coal') != (
        fingerprint(30010, actual_receipt='11:factory_insert:utility:boiler:coal'))


def test_power_gather_guidance_defers_to_separately_qualified_local_target_closure():
    from jev_factorio.judgments import _qualified_utility_power_dependency, question_batch
    from jev_factorio.planning.decision_support import candidate_evidence, scheduling_context
    from test_utility_power_prerequisite_evidence import fixture

    data, state, goal = fixture(fuel=0, coal=0)
    state.factory['inventory_insertable'] = {'coal': 50}
    state.factory['inventory_insertable_evidence'] = {
        'schema': 1, 'tick': state.tick, 'session_id': state.session_id,
        'actor_unit': 9001, 'surface_index': 1, 'force_index': 1,
        'inventory': 'character_main', 'quality': 'normal',
        'method': 'get_insertable_count', 'items': {'coal': 50},
        'basis': 'native_insertable_count_estimate',
    }
    plan = ReadyWorkPlanner(data, state, goal).plan()
    row = candidate_evidence(state, data, [plan])[plan.id]
    assert _qualified_utility_power_dependency(plan, row, state.tick)
    assert row['local_target_completion_evidence'][
        'would_close_current_shortfall_if_native_inventory_verifies'] is True
    _, questions, _ = question_batch({
        'facts': state.for_jev(), **scheduling_context(state, data, [plan], goal),
    }, [plan])
    guidance = questions[plan.id + '/benefit']['instructions']
    assert 'This dependency alone supports useful prerequisite progress (score level 1)' in guidance
    assert 'use that closure evidence when present' in guidance
    assert 'only if a fresh native inventory observation confirms the target threshold' in guidance


@pytest.mark.parametrize('field,value', [('observed_tick', 9), ('session_id', 'different-session')])
def test_power_child_qualifier_rejects_stale_or_foreign_gather_witness(field, value):
    from jev_factorio.judgments import _qualified_utility_power_dependency
    from test_utility_power_prerequisite_evidence import _engine_bill_child

    _, state, plan, row = _engine_bill_child('raw_gather')
    assert _qualified_utility_power_dependency(plan, row, state.tick)
    changed = deepcopy(row)
    changed['gather_start_evidence'][field] = value
    changed['utility_power_prerequisite_start_evidence']['child_start_evidence'][
        'witnesses']['gather_start_evidence'][field] = value
    assert not _qualified_utility_power_dependency(plan, changed, state.tick)
