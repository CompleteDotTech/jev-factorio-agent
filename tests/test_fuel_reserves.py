"""Bounds, deadline and budget regressions for the actual service planner."""
from copy import deepcopy
from dataclasses import replace

import pytest

from jev_factorio.planning.fuel_failure_budget import acquisition_failures
from test_fuel_history import measured_state
from test_fuel_service_bounds import direct
from test_factory import machine


def plan_with_history():
    state, data, history = measured_state()
    state.factory['research'] = ''
    return state, data, direct(state, data)


def test_observed_depletion_and_estimated_lead_add_bounded_carried_reserve():
    state, data, plan = plan_with_history()
    policy = plan.materials['fuel_service']
    assert 8 < plan.steps[0].parameters['quantity'] <= 28
    assert policy['combined_deficit'] == 8
    assert policy['reserve'] > 0
    assert policy['reserve_basis'].startswith('observed_inventory_depletion_proxy')
    assert policy['lead_ticks_estimate'] > 0


def test_capacity_caps_reserve_before_planning_not_only_at_dispatch():
    state, data, _ = plan_with_history()
    state.factory['inventory_insertable'] = {'coal': 9}
    plan = direct(state, data)
    assert plan.steps[0].parameters['quantity'] == 9
    assert plan.materials['fuel_service']['reserve'] == 1


@pytest.mark.parametrize('deadline', ['science', 'boiler'])
def test_existing_science_or_power_need_defers_optional_reserve(deadline):
    state, data, _ = plan_with_history()
    if deadline == 'science':
        state.factory['research'] = 'study'
    else:
        state.factory['entities']['utility:boiler'] = machine('boiler', unit_number=901, fuel={})
    plan = direct(state, data)
    # An unknown science deadline admits the required burner only; a power
    # need without a scheduled science deadline still groups due burners.
    assert plan.steps[0].parameters['quantity'] == (4 if deadline == 'science' else 8)
    assert plan.materials['fuel_service']['reserve'] == 0
    if deadline == 'science':
        assert plan.materials['fuel_service']['deferred']['science_deadline_or_unknown_lead'] == 1
    else:
        assert plan.materials['fuel_service']['reserve_basis'] == 'science_or_power_deadline_defers_optional_reserve'


def test_no_extra_mining_while_useful_carried_stock_remains():
    state, data, _ = plan_with_history()
    state.inventory['coal'] = 3
    plan = direct(state, data)
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters['quantity'] == 3


def test_reserve_quantity_changes_cannot_escape_old_site_failure_counts():
    state, data, high = plan_with_history()
    state._fuel_service_history = {}
    low = direct(state, data)
    assert low.id != high.id  # Existing native inventory-target identities are retained.
    failures = {low.id: 1, high.id: 1}
    assert acquisition_failures(high, failures) == 2
    assert acquisition_failures(low, failures) == 2
    assert failures == {low.id: 1, high.id: 1}


def test_unrelated_site_is_not_charged_but_legacy_unsited_budget_is_retained():
    state, data, original = plan_with_history()
    state.factory['fair_resource_targets']['coal']['position']['x'] += 1
    other = direct(state, data)
    assert acquisition_failures(other, {original.id: 2}) == 0
    assert acquisition_failures(other, {'factory:factory_gather:coal': 2}) == 2


def test_existing_receipt_proven_remainder_budget_semantics_stay_unchanged():
    _, _, original = plan_with_history()
    remainder = replace(original, id=original.id + ':remainder-quantity:1')
    assert acquisition_failures(remainder, {original.id: 2}) == 0
    assert acquisition_failures(remainder, {original.id: 2, remainder.id: 2}) == 2
