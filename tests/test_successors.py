"""Actual Python planners/controller over synthetic, session-bound observations."""
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from jev_factorio import successors
from jev_factorio.background import BackgroundWorkLoop
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.factory_contract import allowed
from jev_factorio.input_controller import input_loop_type
from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.successors import SuccessorPlanner, marked
from jev_factorio.successor_controller import successor_loop_type, _empty_receipts, _extend_paid
from jev_factorio.skills import Plan, Step
from input_routes_fixtures import full, commission, SOURCE
from test_factory import catalog, machine, recipe, snapshot
from test_input_route_integration import game, native_catalog

GROWTH = 'growth:iron-plate'
BASE = input_loop_type(buffered_loop_type(BackgroundWorkLoop))
KIND = successor_loop_type(BASE)


def empty_state():
    state = snapshot(tick=300, inventory={'coal': 50, 'iron-ore': 50})
    state.factory['entities'][SOURCE] = machine(unit_number=500, recipe='iron-plate', products_finished=100,
                                              position={'x': 160, 'y': 160}, fuel={'coal': 50})
    for capability in ('successors', 'input_routes', 'output_buffers', 'production_sites'):
        state.factory[capability] = {'protocol': 1, 'session_id': state.session_id, 'tick': state.tick, 'sources': {}}
    return state


def site_offer(state):
    site = {'state': 'proposed', 'reason': 'joint_layout_available', 'anchor': 'cell-site:iron:8:0',
            'position': {'x': 8, 'y': 0}, 'belt_count': 3, 'components': [],
            'bill': {'stone-furnace': 1, 'burner-mining-drill': 1, 'burner-inserter': 2,
                     'wooden-chest': 1, 'transport-belt': 3}}
    state.factory['production_sites']['sources'][GROWTH] = site
    return site


def flowing_state():
    state = game();full(state);commission(state)
    entity = state.factory['entities'].pop(SOURCE)
    state.factory['entities'][GROWTH] = entity
    state.factory['entities'][SOURCE] = machine(unit_number=500, recipe='iron-plate', products_finished=100,
                                               position={'x': 160, 'y': 160}, fuel={'coal': 50})
    for capability in ('input_routes', 'output_buffers'):
        row = state.factory[capability]['sources'].pop(SOURCE)
        row['source'] = GROWTH
        state.factory[capability]['sources'][GROWTH] = row
    state.factory['production_sites'] = {'protocol': 1, 'session_id': state.session_id, 'tick': state.tick, 'sources': {}}
    site = site_offer(state);site.update(state='owned', source_unit=17, position=entity['position'])
    row = {'source': GROWTH, 'item': 'iron-plate', 'anchor': site['anchor'], 'predecessor': SOURCE,
           'predecessor_unit': 500, 'source_unit': 17, 'paid': 1, 'phase': 'producing', 'seeded': 10,
           'trial_collected': 0, 'credit': 0, 'attribution_resets': 0, 'started_tick': 0, 'remaining_ore': 1000,
           'use': {}, 'qualification': {}}
    state.factory['successors'] = {'protocol': 1, 'session_id': state.session_id, 'tick': state.tick, 'sources': {GROWTH: row}}
    state.factory['entities']['out:chest']['output'] = {'iron-plate': 40}
    return state, row


def qualify(state, row):
    state.tick = 36600
    for cap in ('input_routes', 'output_buffers', 'production_sites', 'successors'):
        state.factory[cap]['tick'] = state.tick
    row['use'] = {'job_id': 'real-receipt', 'recipe': 'iron-gear-wheel', 'quantity': 6, 'source_unit': 17,
                  'input_layout': 'input:17:1', 'completed_tick': 300, 'requested': 3, 'finished': 3,
                  'outputs': {'iron-gear-wheel': 3}}
    row.update(phase='preferred', qualification={'first_tick': 300, 'last_tick': 36300,
        'positive_samples': 30, 'produced': 30, 'source_unit': 17,
        'input_layout': 'input:17:1', 'use_job_id': 'real-receipt'})


def test_trial_collection_is_bounded_and_is_not_qualification():
    state, row = flowing_state()
    assert successors.flowing(GROWTH, state) and not successors.qualified(GROWTH, state)
    plan = SuccessorPlanner(native_catalog(), state, 'rocket_launch')._need('iron-plate', 20)
    assert plan.steps[0].parameters['role'] == 'out:chest'
    assert plan.materials['successor_supply']['kind'] == 'bounded_trial'
    assert plan.steps[0].allowed(state)
    row['trial_collected'] = 200
    assert not plan.steps[0].allowed(state)
    fallback = SuccessorPlanner(native_catalog(), state, 'rocket_launch')._need('iron-plate', 20)
    assert fallback.steps[0].parameters.get('role') != 'out:chest'


def test_preferred_producer_keeps_separate_identity_and_empty_output_falls_back():
    state, row = flowing_state();qualify(state, row)
    before = deepcopy(state)
    assert successors.qualified(GROWTH, state)
    plan = SuccessorPlanner(native_catalog(), state, 'rocket_launch')._need('iron-plate', 20)
    assert plan.materials['successor_supply']['kind'] == 'qualified'
    assert state == before
    state.factory['entities']['out:chest']['output'] = {}
    state.factory['entities'][SOURCE]['output'] = {'iron-plate': 5}
    plan = SuccessorPlanner(native_catalog(), state, 'rocket_launch')._need('iron-plate', 5)
    assert plan.steps[0].parameters['role'] == SOURCE
    assert state.factory['entities'][SOURCE]['unit_number'] == 500


def test_generic_planner_and_ledger_do_not_spend_uncollected_trial_inventory():
    state, _ = flowing_state()
    ledger = SupplyLedger.capture(state, native_catalog())
    assert ledger.collectible.get('iron-plate', 0) == 0
    plan = FactoryPlanner(native_catalog(), state, 'rocket_launch')._need('iron-plate', 10)
    assert plan.steps[0].parameters.get('role') != 'out:chest'
    assert allowed('factory_gather', {'resource': 'iron-ore', 'quantity': 10}, state)


@pytest.mark.parametrize('change', [
    lambda s, r: r.update(phase='preferred'),
    lambda s, r: r.update(source_unit=500),
    lambda s, r: r.update(paid=True),
    lambda s, r: r.update(seeded=11),
    lambda s, r: r.update(fault='identity_changed'),
    lambda s, r: s.factory['successors'].update(tick=s.tick-1),
    lambda s, r: s.factory['entities'][SOURCE].update(unit_number=501),
    lambda s, r: r.update(use={'job_id': 'unverified'}),
])
def test_invalid_successor_evidence_cannot_become_execution_authority(change):
    state, row = flowing_state();change(state, row)
    with pytest.raises(ValueError): successors.sources(state)
    assert not allowed('factory_extract', {'role': 'out:chest', 'item': 'iron-plate', 'quantity': 1, 'receipt': 'x'}, state)


@pytest.mark.parametrize('field,value', [('last_tick', 1000), ('positive_samples', 2),
    ('produced', 2), ('source_unit', 500), ('use_job_id', 'other'), ('input_layout', 'other')])
def test_qualification_requires_window_production_identity_and_same_use_witness(field, value):
    state, row = flowing_state();qualify(state, row);row['qualification'][field] = value
    with pytest.raises(ValueError): successors.sources(state)


def test_complete_kit_precedes_first_paid_successor_and_keeps_science_reserve():
    state = empty_state();site = site_offer(state)
    state.inventory = successors.initial_kit(site, 'iron-ore')
    planner = SuccessorPlanner(native_catalog(), state, 'rocket_launch')
    plan = planner.continuation(GROWTH, site['anchor'])
    assert plan.steps[0].action == 'factory_place' and plan.steps[0].allowed(state)
    assert plan.steps[0].parameters['role'] == GROWTH
    state.inventory['transport-belt'] -= 1
    assert not plan.steps[0].allowed(state)
    state.factory.pop('successors')
    assert not allowed('factory_place', plan.steps[0].parameters, state)


def test_output_commissioning_waits_do_not_accidentally_satisfy_route_discovery():
    state, row = flowing_state()
    state.factory['input_routes']['sources'] = {}
    planner = SuccessorPlanner(native_catalog(), state, 'rocket_launch')
    plan = planner.continuation(GROWTH, row['anchor'])
    assert plan.steps[0].effect == 'successor_route_available'
    assert not plan.steps[0].satisfied(state)


def test_idle_checkpoint_extension_preserves_failures_and_old_reader_rejects_it(tmp_path):
    path = tmp_path / 'memory.json'
    old = BASE.memory_type('s', 'rocket_launch', last_tick=300)
    old.failures['historical-plan'] = 2
    old.save(path)
    new = KIND.memory_type.load(path, 's', 'rocket_launch')
    assert new.failures == old.failures and new.successor_projects == {}
    new.save(path)
    with pytest.raises(ValueError): BASE.memory_type.load(path, 's', 'rocket_launch')
    restored = KIND.memory_type.load(path, 's', 'rocket_launch')
    assert restored.failures == old.failures


def test_paused_checkpoint_and_paid_receipts_survive_restart(tmp_path):
    path = tmp_path / 'memory.json'
    memory = KIND.memory_type('s', 'rocket_launch', last_tick=300)
    memory.successor_projects[GROWTH] = {'anchor': 'cell-site:iron', 'predecessor_unit': 500,
        'source_unit': 17, 'started_tick': 0, 'deadline_tick': 216000, 'status': 'paused'}
    receipt = _empty_receipts();receipt['output_layout'] = 'output:17'
    receipt['output']['chest'] = {'role': 'out:chest', 'receipt': 'paid', 'unit_number': 19, 'paid': 1}
    memory.successor_receipts[GROWTH] = receipt
    memory.failures['successor:' + GROWTH] = 2
    memory.save(path)
    restored = KIND.memory_type.load(path, 's', 'rocket_launch')
    assert restored.successor_projects == memory.successor_projects
    assert restored.successor_receipts == memory.successor_receipts
    assert restored.failures == memory.failures


def test_receipt_advancement_requires_the_exact_pending_native_action():
    paid = {'chest': {'role': 'out:chest', 'receipt': 'paid', 'unit_number': 19, 'paid': 1}}
    step = {'action': 'factory_buffer_build', 'parameters': {
        'source': GROWTH, 'layout': 'output:17', 'part': 'chest', 'receipt': 'paid'}}
    _extend_paid({}, paid, step, GROWTH, 'factory_buffer_build', 'output:17')
    _extend_paid(paid, paid, {}, GROWTH, 'factory_buffer_build', 'output:17')
    with pytest.raises(ValueError): _extend_paid({}, paid, {}, GROWTH, 'factory_buffer_build', 'output:17')
    with pytest.raises(ValueError): _extend_paid(paid, {}, {}, GROWTH, 'factory_buffer_build', 'output:17')


class Backend:
    successors_supported = craft_jobs_supported = input_routes_supported = output_buffers_supported = True
    def __init__(self, state): self.state, self.calls = state, []
    def enable_factory(self): return native_catalog()
    def observe(self): return deepcopy(self.state)
    def act(self, action): return 'synthetic wait'
    def execute(self, action, p):
        assert action == 'factory_place'
        self.calls.append((action, deepcopy(p)))
        site = self.state.factory['production_sites']['sources'][GROWTH]
        self.state.inventory['stone-furnace'] -= 1
        self.state.factory['entities'][GROWTH] = machine(unit_number=17, position=site['position'], products_finished=0)
        site.update(state='owned', source_unit=17)
        self.state.factory['successors']['sources'][GROWTH] = {'source': GROWTH, 'item': 'iron-plate',
            'anchor': site['anchor'], 'predecessor': SOURCE, 'predecessor_unit': 500, 'source_unit': 17,
            'paid': 1, 'phase': 'output_building', 'seeded': 0, 'trial_collected': 0, 'credit': 0,
            'attribution_resets': 0, 'started_tick': self.state.tick, 'remaining_ore': 0, 'use': {}, 'qualification': {}}
        raise TimeoutError('Synthetic lost acknowledgment after native paid placement')


def test_actual_controller_lost_furnace_acknowledgment_reconciles_without_rebuilding(tmp_path):
    state = empty_state();site = site_offer(state)
    state.inventory = successors.initial_kit(site, 'iron-ore')
    backend = Backend(state)
    def loop(resume=False):
        instance = KIND(backend, policy='deterministic', target='rocket_launch', factory_scheduling='ready-work',
                        tick_seconds=0, checkpoint=str(tmp_path / 'memory.json'), resume_controller=resume)
        if not resume:
            instance.memory = instance.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch',
                completed_goals={key: 0 for key in instance.order[:-1]}, last_tick=state.tick)
            instance.memory.failures['old-failure'] = 2
        planner = SuccessorPlanner(native_catalog(), state, 'rocket_launch')
        if not resume:
            plan = marked(planner.continuation(GROWTH, site['anchor']), GROWTH, site, state)
            instance._work_candidates = lambda observation: ([plan], '')
        return instance
    first = loop()
    record = first.step()
    assert not record['verified'] and first.memory.pending['dispatch'] == 'ambiguous'
    resumed = loop(True)
    assert resumed.step()['verified']
    assert len(backend.calls) == 1 and state.inventory['stone-furnace'] == 0
    assert resumed.memory.successor_projects[GROWTH]['source_unit'] == 17
    assert resumed.memory.failures['old-failure'] == 2
    assert state.factory['entities'][SOURCE]['unit_number'] == 500


def test_non_successor_controller_refuses_world_with_retained_successor_state():
    backend = Backend(empty_state())
    loop = BASE(backend, policy='deterministic', target='rocket_launch', factory_scheduling='ready-work')
    with pytest.raises(ValueError, match='explicit controller capability'): loop._observe()
