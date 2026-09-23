"""Sampled native-shaped fixtures; no live throughput claims or provider calls."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.controller import HierarchicalLoop
from jev_factorio.planning.capacity_evidence import CapacityHistory, capacity_evidence, MAX_SAMPLES, MAX_PRODUCERS
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from test_economic_production import economic_catalog, economic_state
from test_factory import machine, recipe

ROLE = 'recipe:iron-plate'
EXTRA = 'capacity:iron-plate:2'


def fixture():
    data, state = economic_catalog(), economic_state()
    data.recipes['iron-plate']['energy'] = 10
    state.inventory.update({'iron-ore': 50, 'stone-furnace': 1})
    state.factory['entities'][ROLE] = machine(recipe='iron-plate', input={'iron-ore': 30},
        fuel={'coal': 50}, crafting=True, products_finished=100)
    return data, state


def observe(history, state, data, index):
    state.tick = 10 + index * 600
    state.factory['entities'][ROLE]['products_finished'] = 100 + index
    return history.observe(state, data)


def warm(data, state):
    history = CapacityHistory()
    for i in range(3):
        observe(history, state, data, i)
    return history


def planner(data, state):
    result = ReadyWorkPlanner(data, state, 'rocket_launch')
    result._economic_products = {'iron-plate': 200}
    return result


def candidate(data, state):
    worker = planner(data, state)
    wait = worker._wait('machine_output', 'iron-plate', 10, ROLE)
    return worker._capacity_work(wait)


def test_positive_counter_growth_and_removed_output_support_bounded_expansion():
    data, state = fixture()
    warm(data, state)
    evidence = capacity_evidence(state, data, ROLE)
    assert evidence['samples'] == 3 and evidence['span_ticks'] == 1200
    assert evidence['products_delta'] == evidence['removed_from_cell'] == 2
    assert evidence['observed_products_per_tick'] == 1 / 600
    plan = candidate(data, state)
    assert plan.steps[0].action == 'factory_place'
    assert plan.steps[0].parameters['role'] == EXTRA
    assert plan.steps[0].costs == {'stone-furnace': 1}
    assert plan.materials['economics']['measured_bottleneck'] == evidence
    assert not plan.steps[0].satisfied(state)
    assert EXTRA not in state.factory['entities']


@pytest.mark.parametrize('count', [0, 1, 2])
def test_one_snapshot_or_short_history_never_authorizes_extra_capacity(count):
    data, state = fixture()
    history = CapacityHistory()
    for i in range(count):
        observe(history, state, data, i)
    assert capacity_evidence(state, data, ROLE) is None
    assert candidate(data, state).steps[0].action == 'factory_wait'


def test_observation_does_not_change_authoritative_snapshot_or_count_duplicate_ticks():
    data, state = fixture()
    original = deepcopy(asdict(state))
    history = CapacityHistory()
    for _ in range(100):
        history.observe(state, data)
    assert asdict(state) == original
    assert len(history.rows[ROLE]) == 1
    assert candidate(data, state).steps[0].action == 'factory_wait'


@pytest.mark.parametrize('change', ['session', 'backwards_tick', 'gap', 'unit', 'recipe', 'counter', 'name', 'disappeared'])
def test_discontinuities_invalidate_capacity_history(change):
    data, state = fixture()
    history = warm(data, state)
    if change == 'session': state.session_id = 'replacement'
    elif change == 'backwards_tick': state.tick = 1
    elif change == 'gap': state.tick += 901
    elif change == 'unit': state.factory['entities'][ROLE]['unit_number'] += 1
    elif change == 'recipe':
        data.recipes['other'] = recipe('other', {'iron-ore': 1}, 'smelting')
        state.factory['entities'][ROLE]['recipe'] = 'other'
    elif change == 'counter': state.factory['entities'][ROLE]['products_finished'] = 1
    elif change == 'name': state.factory['entities'][ROLE]['name'] = 'other'
    elif change == 'disappeared': state.factory['entities'].pop(ROLE)
    history.observe(state, data)
    assert capacity_evidence(state, data, ROLE) is None


@pytest.mark.parametrize('change', ['input', 'fuel', 'crafting', 'backlog', 'slow', 'fast'])
def test_supply_transport_and_rate_constraints_are_not_extra_capacity_proof(change):
    data, state = fixture()
    history = CapacityHistory()
    for i in range(3):
        state.tick = 10 + i * 600
        producer = state.factory['entities'][ROLE]
        producer['products_finished'] = 100 + (i * 10 if change == 'fast' else i // 2 if change == 'slow' else i)
        if i == 1 and change == 'input': producer['input'] = {}
        if i == 1 and change == 'fuel': producer['fuel'] = {'coal': 0}
        if change == 'crafting': producer['crafting'] = False
        if change == 'backlog': producer['output'] = {'iron-plate': i}
        history.observe(state, data)
        producer['input'], producer['fuel'] = {'iron-ore': 30}, {'coal': 50}
    assert capacity_evidence(state, data, ROLE) is None
    assert candidate(data, state).steps[0].action == 'factory_wait'


@pytest.mark.parametrize('field,value', [('products_finished', True), ('products_finished', -1),
    ('products_finished', 1.2), ('unit_number', None), ('input', None), ('output', None),
    ('crafting', 'true'), ('input', {'iron-ore': float('nan')}), ('fuel', {'coal': float('inf')})])
def test_malformed_evidence_clears_history(field, value):
    data, state = fixture()
    history = warm(data, state)
    state.factory['entities'][ROLE][field] = value
    evidence = history.observe(state, data)
    assert evidence['producers'][ROLE]['reason'] == 'missing_or_unsupported_evidence'
    assert ROLE not in history.rows


@pytest.mark.parametrize('change', ['multiple_products', 'probabilistic', 'two_units', 'locked', 'missing_speed'])
def test_unsupported_recipe_counter_semantics_remain_unknown(change):
    data, state = fixture()
    rec = data.recipes['iron-plate']
    if change == 'multiple_products': rec['products'].append(dict(rec['products'][0], name='other'))
    elif change == 'probabilistic': rec['products'][0]['probability'] = 0.5
    elif change == 'two_units': rec['products'][0]['amount'] = 2
    elif change == 'locked': rec['enabled'] = False
    elif change == 'missing_speed': data.machines['stone-furnace'].pop('speed')
    warm(data, state)
    assert capacity_evidence(state, data, ROLE) is None


def test_current_fuel_failure_is_not_hidden_by_sampling_interval():
    data, state = fixture()
    history = warm(data, state)
    state.tick += 1
    state.factory['entities'][ROLE]['fuel']['coal'] = 0
    history.observe(state, data)
    assert len(history.rows[ROLE]) == 1
    assert capacity_evidence(state, data, ROLE) is None


def test_recipe_and_buffer_share_one_output_ledger():
    data, state = fixture()
    state.factory['output_buffers'] = {'sources': {ROLE: {
        'source_unit': 17, 'state': 'ready', 'chest_role': 'output-chest:17'}}}
    state.factory['entities']['output-chest:17'] = machine('wooden-chest', unit_number=18, output={})
    history = CapacityHistory()
    for i in range(3):
        state.factory['entities']['output-chest:17']['output']['iron-plate'] = i
        observe(history, state, data, i)
    assert state._capacity_evidence['producers'][ROLE]['removed_from_cell'] == 0
    assert capacity_evidence(state, data, ROLE) is None


def test_duplicate_entity_aliases_and_long_histories_are_bounded():
    data, state = fixture()
    history = warm(data, state)
    state.factory['entities']['capacity:iron-plate:2'] = deepcopy(state.factory['entities'][ROLE])
    history.observe(state, data)
    assert capacity_evidence(state, data, ROLE) is None
    state.factory['entities'].pop(EXTRA)
    for i in range(100): observe(history, state, data, i)
    assert len(history.rows[ROLE]) == MAX_SAMPLES
    for i in range(100):
        state.factory['entities'][f'capacity:z:{i}'] = machine(recipe='iron-plate', unit_number=200+i,
            input={'iron-ore': 50}, fuel={'coal': 50}, crafting=True, products_finished=100)
    history.observe(state, data)
    assert len(history.rows) <= MAX_PRODUCERS


@pytest.mark.parametrize('change', ['tick', 'session', 'public_payload', 'new_process'])
def test_stale_or_public_payload_cannot_replace_process_local_evidence(change):
    data, state = fixture()
    warm(data, state)
    if change == 'tick': state.tick += 1
    elif change == 'session': state.session_id = 'other'
    elif change == 'public_payload':
        state.factory['capacity_evidence'] = state._capacity_evidence
        del state._capacity_evidence
    elif change == 'new_process': CapacityHistory().observe(state, data)
    assert capacity_evidence(state, data, ROLE) is None


def test_research_wait_can_offer_justified_ready_kit_without_switching_research():
    data, state = fixture()
    warm(data, state)
    worker = planner(data, state)
    wait = worker._wait('research_progress', 'study', 0.1)
    result = worker._capacity_work(wait)
    assert result.steps[0].action == 'factory_place'
    assert result.steps[0].parameters['role'] == EXTRA
    assert state.factory['research'] == 'study'


def test_no_uncommitted_kit_acquisition_or_third_cell():
    data, state = fixture()
    warm(data, state)
    state.inventory.pop('stone-furnace')
    assert candidate(data, state).steps[0].action == 'factory_wait'
    state.factory['entities'][EXTRA] = machine(recipe='iron-plate', unit_number=31,
        output={'iron-plate': 10}, fuel={'coal': 50})
    del state._capacity_evidence
    result = candidate(data, state)
    assert result.steps[0].action == 'factory_extract'
    assert result.steps[0].parameters['role'] == EXTRA


@pytest.mark.parametrize('preflight_fuel', [0, 50])
def test_real_controller_samples_only_existing_observations_and_rechecks_before_build(tmp_path, preflight_fuel):
    data, state = fixture()
    class Backend:
        calls = 0
        actions = []
        def enable_factory(self): return data
        def observe(self):
            self.calls += 1
            value = deepcopy(state)
            value.tick = self.calls * 600
            value.factory['entities'][ROLE]['products_finished'] = 100 + self.calls
            if self.calls >= 4: value.factory['entities'][ROLE]['fuel']['coal'] = preflight_fuel
            return value
        def execute(self, action, parameters):
            self.actions.append(action)
            assert action == 'factory_place'
            state.inventory[parameters['name']] -= 1
            state.factory['entities'][parameters['role']] = machine(parameters['name'], unit_number=31)
            return 'paid placement'
    class Loop(HierarchicalLoop):
        def _refresh_goals(self, snapshot): self.memory.active_goal = 'rocket_launch'
        def _compile_candidates(self, snapshot): return [candidate(data, snapshot)], ''
    backend = Backend()
    loop = Loop(backend, policy='deterministic', factory_scheduling='ready-work', checkpoint=str(tmp_path/'state.json'))
    loop._observe(); loop._observe()
    record = loop.step()
    if preflight_fuel:
        assert record['verified'] and backend.actions == ['factory_place']
        assert backend.calls == 5  # Two warmups plus existing before/pre/post observations.
    else:
        assert backend.actions == [] and backend.calls == 4
    assert record['performance']['calls']['observation']['count'] == backend.calls - 2


def test_sub_sample_counter_regression_and_recovered_outage_do_not_reuse_old_proof():
    data, state = fixture()
    history = warm(data, state)
    state.tick += 1
    state.factory['entities'][ROLE]['products_finished'] = 105
    history.observe(state, data)
    state.tick += 1
    state.factory['entities'][ROLE]['products_finished'] = 104
    history.observe(state, data)
    assert len(history.rows[ROLE]) == 1
    assert capacity_evidence(state, data, ROLE) is None
    history = warm(data, state)
    state.tick += 1
    state.factory['entities'][ROLE]['fuel']['coal'] = 0
    history.observe(state, data)
    state.tick += 1
    state.factory['entities'][ROLE]['fuel']['coal'] = 50
    history.observe(state, data)
    assert capacity_evidence(state, data, ROLE) is None


@pytest.mark.parametrize('change', ['spare_input', 'buffered_input', 'demand'])
def test_fresh_preflight_rechecks_more_than_historical_utilization(change):
    from jev_factorio.planning.capacity_evidence import expansion_ready
    data, state = fixture()
    warm(data, state)
    assert expansion_ready(state, data, ROLE)
    if change == 'spare_input': state.inventory['iron-ore'] = 0
    elif change == 'buffered_input': state.factory['entities'][ROLE]['input']['iron-ore'] = 2
    else: state.factory['research_progress'] = 0.99
    assert not expansion_ready(state, data, ROLE)
