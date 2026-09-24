"""Fail-closed optional service limits; original transfers are not suppressed."""
import pytest

from jev_factorio.planning.service_policy import ServiceBudget
from jev_factorio.planning.service_visits import service_visit
from test_factory import machine
from test_phase2_automation import lab_fixture


@pytest.mark.parametrize('fuel', [{}, {'coal': 0}, {'coal': None}, {'coal': True},
                                  {'coal': -1}, {'coal': float('nan')}, {'coal': float('inf')}])
def test_empty_or_invalid_boiler_fuel_does_not_authorize_optional_service(fuel):
    state, _, planner, first = lab_fixture()
    state.factory['entities']['utility:boiler'] = machine('boiler', unit_number=300, fuel=fuel)
    assert service_visit(planner, first) == first


def test_large_factory_falls_back_instead_of_assuming_uninspected_roles_are_safe():
    state, _, planner, first = lab_fixture()
    for number in range(64):
        state.factory['entities'][f'other:{number}'] = machine(unit_number=300+number, fuel={'coal': 50})
    assert service_visit(planner, first) == first


def test_exact_time_budget_is_admitted_and_next_step_is_rejected_without_extra_credit():
    _, _, planner, first = lab_fixture()
    budget = ServiceBudget(planner, first.steps[0], {'utility:lab'})
    step = planner._transfer('utility:lab', 'green', 1).steps[0]
    assert all(budget.admit(step) for _ in range(3))
    assert budget.extra_ticks == 900
    assert not budget.admit(step)
    assert budget.extra_ticks == 900
    assert budget.summary()['rejections'] == {'service_time_budget': 1}
