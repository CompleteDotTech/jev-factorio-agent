"""Observed stock-depletion proxy tests; not native burn measurements."""
from copy import deepcopy

import pytest

from jev_factorio.planning.fuel_history import (FuelHistory, estimated_rate, MAX_GAP_TICKS,
                                               MAX_SAMPLES, BASIS)
from test_grouped_fuel_service import due_scenario


def bind(state):
    state.factory['acceptance_runtime'] = dict(schema=1, session_id=state.session_id,
        actor_unit=1, player_index=1, surface_index=1, force_index=1)


def measured_state():
    state, data = due_scenario()
    bind(state)
    history = FuelHistory()
    roles = ('input:drill', 'input:inserter')
    for tick, fuel in ((0, 3), (300, 2), (600, 1)):
        state.tick = tick
        for capability in ('input_routes', 'output_buffers'):
            state.factory[capability]['tick'] = tick
        for role in roles:
            state.factory['entities'][role]['fuel']['coal'] = fuel
        history.observe(state)
    return state, data, history


def unit(state):
    return state.factory['entities']['input:drill']['unit_number']


def test_rates_are_identity_bound_depletion_estimates_not_a_new_native_counter():
    state, _, history = measured_state()
    assert estimated_rate(state, unit(state)) == pytest.approx(2 / 600)
    assert state._fuel_service_history['basis'] == BASIS
    assert '_fuel_service_history' not in state.for_jev()
    assert 'unit_number' not in str(history.observe(state))


@pytest.mark.parametrize('change', ['session', 'actor', 'surface', 'force', 'player', 'unit', 'position', 'gap', 'regression', 'ambiguous'])
def test_identity_gaps_and_ambiguous_mutations_invalidate_history(change):
    state, _, history = measured_state()
    runtime = state.factory['acceptance_runtime']
    if change == 'session':
        state.session_id = runtime['session_id'] = 'new-fixture'
    elif change in {'actor', 'surface', 'force', 'player'}:
        runtime[{'actor':'actor_unit', 'surface':'surface_index', 'force':'force_index', 'player':'player_index'}[change]] += 1
    elif change == 'unit':
        state.factory['entities']['input:drill']['unit_number'] += 500
    elif change == 'position':
        state.factory['entities']['input:drill']['position']['x'] += 1
    elif change == 'gap':
        state.tick += MAX_GAP_TICKS + 1
    elif change == 'regression':
        state.tick -= 1
    history.observe(state, ambiguous=change == 'ambiguous')
    assert estimated_rate(state, unit(state)) is None


@pytest.mark.parametrize('fuel', [0, 5, True, -1, float('nan')])
def test_refill_empty_censored_or_malformed_samples_are_not_burn_proof(fuel):
    state, _, history = measured_state()
    state.factory['entities']['input:drill']['fuel']['coal'] = fuel
    state.tick += 300
    history.observe(state)
    assert estimated_rate(state, unit(state)) is None


def test_conflicting_alias_resets_all_derived_rates():
    state, _, history = measured_state()
    state.factory['entities']['alias'] = deepcopy(state.factory['entities']['input:drill'])
    state.factory['entities']['alias']['fuel']['coal'] = 5
    summary = history.observe(state)
    assert summary['eligible_consumers'] == 0
    assert not history._samples


def test_memory_is_bounded_and_restart_never_claims_old_rates():
    state, _ = due_scenario()
    bind(state)
    history = FuelHistory()
    for i in range(100):
        state.tick = i * 300
        state.factory['entities']['input:drill']['fuel']['coal'] = 200 - i
        history.observe(state)
    assert len(history._samples[unit(state)][1]) == MAX_SAMPLES
    FuelHistory().observe(state)
    assert estimated_rate(state, unit(state)) is None


def test_rates_are_not_reused_on_a_new_unobserved_snapshot_tick():
    state, _, _ = measured_state()
    state.tick += 1
    assert estimated_rate(state, unit(state)) is None


def test_same_tick_changed_stock_resets_instead_of_dividing_by_zero():
    state, _, history = measured_state()
    state.factory['entities']['input:drill']['fuel']['coal'] = 2
    history.observe(state)
    assert estimated_rate(state, unit(state)) is None
