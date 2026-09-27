"""Issue #99: deterministic quantity/ownership regressions, not a native run."""
from copy import deepcopy

import pytest

from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.fuel_service import service_plan
from test_maintenance_progress import progress_scenario


def due_scenario(coal=0):
    backend, data = progress_scenario(coal=coal)
    state = backend.state
    state.factory['entities']['out:chest']['output'].clear()
    state.factory['entities']['input:inserter']['fuel']['coal'] = 1
    return state, data


def test_combined_eight_coal_deficit_instead_of_repeated_four_coal_trips():
    state, data = due_scenario()
    plan = InputRoutePlanner(data, state, 'rocket_launch')._need('iron-plate', 10)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters['quantity'] == 8
    assert plan.materials['fuel_service']['combined_deficit'] == 8
    assert plan.materials['fuel_service']['reserve_basis'].startswith('unknown_burn_rate')


def test_one_gather_then_two_distinct_sequential_refills_retain_remaining_coal():
    state, data = due_scenario()
    actions = []
    for _ in range(3):
        plan = InputRoutePlanner(data, state, 'rocket_launch')._need('iron-plate', 10)
        step = plan.steps[0]
        actions.append((step.action, dict(step.parameters)))
        if step.action == 'factory_gather':
            state.inventory['coal'] += step.parameters['quantity']
        else:
            n = step.parameters['quantity']
            state.inventory['coal'] -= n
            state.factory['entities'][step.parameters['role']]['fuel']['coal'] += n
    assert [action for action,_ in actions] == ['factory_gather','factory_insert','factory_insert']
    assert len({args['role'] for action,args in actions if action=='factory_insert'}) == 2
    assert state.inventory['coal'] == 0


def test_held_coal_is_not_spent_or_counted_twice():
    state, data = due_scenario(10)
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    planner.ledger = SupplyLedger.capture(state, data, reserved={'coal':10})
    plan = planner._need('iron-plate',10)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters['quantity'] == 8
    assert plan.steps[0].threshold == 18
    assert plan.materials['fuel_service']['carried_held'] == 10


def test_partial_carried_fuel_is_useful_without_another_acquisition():
    state,data=due_scenario(2)
    plan=InputRoutePlanner(data,state,'rocket_launch')._need('iron-plate',10)
    assert plan.steps[0].action=='factory_insert'
    assert plan.steps[0].parameters['quantity']==2
    assert plan.steps[0].costs=={'coal':2}


@pytest.mark.parametrize('capacity',[0,1,3])
def test_full_or_limited_inventory_is_bounded(capacity):
    state,data=due_scenario()
    state.factory['inventory_insertable']={'coal':capacity}
    planner=InputRoutePlanner(data,state,'rocket_launch')
    if capacity==0:
        with pytest.raises(ValueError,match='capacity'): planner._need('iron-plate',10)
    else:
        plan=planner._need('iron-plate',10)
        assert plan.steps[0].parameters['quantity']==capacity


def test_alias_does_not_duplicate_native_consumer():
    state,data=due_scenario()
    route=state.factory['input_routes']['sources']['recipe:iron-plate']
    state.factory['entities']['alias']=deepcopy(state.factory['entities']['input:inserter'])
    route['parts']['drill']['role']='alias'
    # Direct policy test: contradictory route topology is separately rejected
    # by the actual route validator. The accounting itself must deduplicate.
    planner=InputRoutePlanner(data,state,'rocket_launch')
    plan=service_plan(planner,'input:inserter','recipe:iron-plate',(),planner._acquire)
    assert plan.materials['fuel_service']['combined_deficit']==4


def test_geometry_unavailable_is_unknown_not_a_zero_cost_reserve():
    state,data=due_scenario()
    state.factory['entities']['input:inserter'].pop('position')
    plan=InputRoutePlanner(data,state,'rocket_launch')._need('iron-plate',10)
    assert plan.materials['fuel_service']['lead_ticks_estimate'] is None
    assert plan.materials['fuel_service']['reserve']==0


def test_ready_science_and_stocked_output_still_avoid_fueling():
    state,data=due_scenario()
    state.factory['entities']['out:chest']['output']['iron-plate']=227
    plan=InputRoutePlanner(data,state,'rocket_launch')._need('iron-plate',10)
    assert plan.steps[0].action=='factory_extract'
    assert 'fuel_service' not in plan.materials


def test_changed_observation_recomputes_due_group_and_plan_roundtrips():
    from jev_factorio.skills import Plan
    state,data=due_scenario()
    first=InputRoutePlanner(data,state,'rocket_launch')._need('iron-plate',10)
    assert first.steps[0].parameters['quantity']==8
    state.factory['entities']['input:inserter']['fuel']['coal']=5
    changed=InputRoutePlanner(data,state,'rocket_launch')._need('iron-plate',10)
    assert changed.steps[0].parameters['quantity']==4
    assert Plan.from_dict(changed.to_dict()).to_dict()==changed.to_dict()


@pytest.mark.parametrize('alias_fuel', [0, 2, 5])
def test_conflicting_native_alias_telemetry_does_not_authorize_service(alias_fuel):
    state,data=due_scenario()
    route=state.factory['input_routes']['sources']['recipe:iron-plate']
    state.factory['entities']['alias']=deepcopy(state.factory['entities']['input:inserter'])
    state.factory['entities']['alias']['fuel']['coal']=alias_fuel
    route['parts']['drill']['role']='alias'
    planner=InputRoutePlanner(data,state,'rocket_launch')
    with pytest.raises(ValueError,match='Aliased'):
        service_plan(planner,'input:inserter','recipe:iron-plate',(),planner._acquire)
