"""A raw-ore service dependency does not imply downstream-arm fuel demand."""
from copy import deepcopy

import pytest

from jev_factorio import mining_outposts, output_buffers
from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from input_routes_fixtures import fixture as buffered_fixture, SOURCE
from test_outpost_maintenance_progress import two_due_outposts


def mixed_fixture(*, ready=100, carried=0, plate_demand=0, coal=0):
    state, catalog, _ = two_due_outposts()
    buffered = buffered_fixture()
    state.factory['output_buffers'] = deepcopy(buffered.factory['output_buffers'])
    state.factory['output_buffers'].update(session_id=state.session_id, tick=state.tick)
    # Move the intact output cell close to the existing outposts. No direct
    # input route is copied: a legacy outpost and that route cannot coexist.
    for role, entity in buffered.factory['entities'].items():
        copied = deepcopy(entity)
        copied['position']['x'] += 120
        state.factory['entities'][role] = copied
    state.factory['entities']['out:chest']['output'] = {'iron-plate': ready}
    state.factory['entities']['out:arm']['fuel'] = {'coal': 1}
    state.inventory.update({'coal': coal, 'iron-plate': carried})
    planner = MiningOutpostPlanner(catalog, state, 'rocket_launch')
    planner._set_focus('iron-ore', 20)
    planner.demands['copper-ore'] = 20
    if plate_demand:
        planner.demands['iron-plate'] = plate_demand
    # Assert valid current contracts, not a fabricated consumer-only map.
    assert len(mining_outposts.sources(state)) == 2
    assert output_buffers.flow_complete(SOURCE, 'output:17', state)
    return state, catalog, planner


@pytest.mark.parametrize('ready,carried,demand', [
    (0, 0, 0), (100, 0, 0), (100_000, 0, 0),
    (100, 0, 20), (20, 0, 20), (0, 20, 20), (10, 20, 20),
])
def test_unneeded_downstream_arm_cannot_inflate_raw_ore_fuel_acquisition(ready, carried, demand):
    state, _, planner = mixed_fixture(ready=ready, carried=carried, plate_demand=demand)
    before = deepcopy(state)
    plan = planner._need('iron-ore', 20)
    assert plan.steps[0].action == 'factory_gather' and plan.steps[0].allowed(state)
    evidence = plan.materials['fuel_service']
    assert evidence['consumer_count'] == 2
    assert evidence['combined_deficit'] == 8
    assert plan.steps[0].parameters['quantity'] == 8
    assert {r['role'] for r in evidence['consumers']} == {
        mining_outposts.role('iron-ore', 'drill'), mining_outposts.role('copper-ore', 'drill')}
    assert state == before


@pytest.mark.parametrize('ready', [0, 10, 19])
def test_genuinely_uncovered_downstream_demand_still_admits_its_arm(ready):
    state, _, planner = mixed_fixture(ready=ready, plate_demand=20)
    plan = planner._need('iron-ore', 20)
    assert plan.steps[0].allowed(state)
    evidence = plan.materials['fuel_service']
    assert evidence['consumer_count'] == 3
    assert evidence['combined_deficit'] == 12
    assert any(r['role'] == 'out:arm' for r in evidence['consumers'])


def test_reserved_carried_plates_do_not_hide_real_downstream_demand():
    state, catalog, planner = mixed_fixture(ready=0, carried=20, plate_demand=20)
    planner.ledger = SupplyLedger.capture(state, catalog, reserved={'iron-plate': 20})
    plan = planner._need('iron-ore', 20)
    assert plan.materials['fuel_service']['consumer_count'] == 3
    assert plan.materials['fuel_service']['combined_deficit'] == 12


def test_partial_coal_is_still_inserted_into_required_primary():
    state, _, planner = mixed_fixture(coal=2)
    plan = planner._need('iron-ore', 20)
    assert plan.steps[0].action == 'factory_insert' and plan.steps[0].allowed(state)
    assert plan.steps[0].parameters['role'] == mining_outposts.role('iron-ore', 'drill')
    assert plan.steps[0].parameters['quantity'] == 2
    assert plan.materials['fuel_service']['combined_deficit'] == 8
