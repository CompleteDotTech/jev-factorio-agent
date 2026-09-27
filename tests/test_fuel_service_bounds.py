"""Bounded manual coal policy; these are deterministic fixtures, not native flow."""
from copy import deepcopy

import pytest

from jev_factorio.planning.fuel_service import service_plan
from jev_factorio.planning.input_routes import InputRoutePlanner
from test_grouped_fuel_service import due_scenario


def direct(state, data):
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    return service_plan(planner, 'input:inserter', 'recipe:iron-plate', (), planner._acquire)


def test_alias_conflict_above_due_threshold_fails_closed():
    state, data = due_scenario()
    route = state.factory['input_routes']['sources']['recipe:iron-plate']
    state.factory['entities']['alias'] = deepcopy(state.factory['entities']['input:inserter'])
    state.factory['entities']['alias']['fuel']['coal'] = 5
    route['parts']['drill']['role'] = 'alias'
    with pytest.raises(ValueError, match='Aliased'):
        direct(state, data)


def test_distant_optional_burner_does_not_inflate_nearby_service_acquisition():
    state, data = due_scenario()
    # Low-level policy test. Actual route geometry has its separate validator.
    state.factory['entities']['input:drill']['position'] = {'x': 10000, 'y': 0}
    plan = direct(state, data)
    assert plan.steps[0].parameters['quantity'] == 4
    assert plan.materials['fuel_service']['deferred']['service_leg_budget'] == 1


def test_failed_optional_transfer_does_not_keep_requesting_its_fuel():
    state, data = due_scenario()
    state._planner_failure_budgets = {'factory:factory_insert:input:drill': 2}
    plan = direct(state, data)
    assert plan.steps[0].parameters['quantity'] == 4
    assert plan.materials['fuel_service']['deferred']['plan_failure_budget'] == 1


def test_failed_primary_transfer_is_not_replaced_by_a_new_identity():
    state, data = due_scenario()
    state._planner_failure_budgets = {'factory:factory_insert:input:inserter': 2}
    with pytest.raises(ValueError, match='primary.*budget'):
        direct(state, data)


def test_known_carried_coal_never_gets_gathered_again_for_an_optional_reserve():
    state, data = due_scenario(1)
    plan = direct(state, data)
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters['quantity'] == 1


@pytest.mark.parametrize('bad', [True, -1, float('inf'), float('nan'), '12'])
def test_malformed_insertable_capacity_cannot_authorize_acquisition(bad):
    state, data = due_scenario()
    state.factory['inventory_insertable'] = {'coal': bad}
    with pytest.raises(ValueError):
        direct(state, data)
