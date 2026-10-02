"""Issue #98: paired composed-controller fixtures, never native acceptance."""
from copy import deepcopy

import pytest

from jev_factorio.background import BackgroundWorkLoop
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.input_controller import input_loop_type
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from test_factory import machine
from test_input_route_integration import RouteBackend, RouteLoop, controller, native_catalog
from input_routes_fixtures import SOURCE, full, commission, row


def progress_scenario(fuel=1, coal=50, science=20):
    backend = RouteBackend()
    state = backend.state
    full(state)
    commission(state)
    state.inventory.update({'coal': coal, 'logistic-science-pack': science})
    state.factory['entities']['input:drill']['fuel']['coal'] = fuel
    state.factory['entities']['utility:lab'] = machine(
        'lab', unit_number=900, energy=100, input={})
    state.factory.update(research='study', research_progress=0)
    state.factory['entities']['out:chest']['output']['iron-plate'] = 227
    state._lead_time_supply = True
    data = native_catalog()
    data.technologies['study'] = dict(enabled=True, effects=[], prerequisites=[],
        ingredients=[{'name': 'logistic-science-pack', 'amount': 1}], count=100, energy_ticks=60)
    data.technologies['rocket-silo'] = dict(enabled=True, effects=[], prerequisites=['study'],
        ingredients=[{'name': 'logistic-science-pack', 'amount': 1}], count=100, energy_ticks=60)
    backend.enable_factory = lambda: data
    return backend, data


@pytest.mark.parametrize('fuel', [2, 1, 0])
@pytest.mark.parametrize('coal', [0, 50])
@pytest.mark.parametrize('background', [False, True])
def test_ready_science_survives_unrelated_low_fuel(tmp_path, fuel, coal, background):
    backend, _ = progress_scenario(fuel, coal)
    kind = input_loop_type(buffered_loop_type(BackgroundWorkLoop)) if background else RouteLoop
    loop = controller(backend, tmp_path, kind=kind)
    before = deepcopy(backend.state)
    plans, blocker = loop._compile_candidates(backend.state)
    assert any(p.steps[0].action == 'factory_insert'
               and p.steps[0].parameters['role'] == 'utility:lab'
               and p.steps[0].parameters['item'] == 'logistic-science-pack' for p in plans), blocker
    assert backend.state == before


@pytest.mark.parametrize('fuel', [2, 1])
def test_ready_science_craft_survives_low_fuel(tmp_path, fuel):
    backend, _ = progress_scenario(fuel, science=0)
    loop = controller(backend, tmp_path, kind=RouteLoop)
    plans, _ = loop._compile_candidates(backend.state)
    assert any(p.steps[0].action == 'factory_craft'
               and p.steps[0].parameters['recipe'] == 'logistic-science-pack' for p in plans)


@pytest.mark.parametrize('kind', [InputRoutePlanner, OutputBufferPlanner])
@pytest.mark.parametrize('component', ['input:drill', 'input:inserter', 'out:arm'])
def test_collect_ready_required_output_before_upstream_refill(kind, component):
    backend, data = progress_scenario()
    backend.state.factory['entities'][component]['fuel']['coal'] = 0
    plan = kind(data, backend.state, 'rocket_launch')._need('iron-plate', 10)
    step = plan.steps[0]
    assert step.action == 'factory_extract'
    assert step.parameters['role'] == 'out:chest'
    assert step.parameters['quantity'] == 10


def test_depleted_output_still_services_required_input_route():
    backend, data = progress_scenario()
    backend.state.factory['entities']['out:chest']['output'].clear()
    plan = InputRoutePlanner(data, backend.state, 'rocket_launch')._need('iron-plate', 10)
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters['role'] == 'input:drill'
    assert plan.steps[0].parameters['item'] == 'coal'


def connected_power_plant(state, *, fuel=1):
    """Model an operating plant, including the actual lab supply topology."""
    state.factory['entities']['utility:lab']['electric_network_id'] = 9
    state.factory['entities'].update({
        'utility:water': machine('offshore-pump', unit_number=902,
            fluid_ports=[{'id': 91, 'fluid': 'water'}]),
        'utility:boiler': machine('boiler', unit_number=901, fuel={'coal': fuel},
            fluid_ports=[{'id': 91, 'fluid': 'water'}, {'id': 92, 'fluid': 'steam'}]),
        'utility:engine': machine('steam-engine', unit_number=903, electric_network_id=9,
            fluid_ports=[{'id': 92, 'fluid': 'steam'}]),
    })


@pytest.mark.parametrize('connected', [False, True])
def test_lab_supply_precedes_boiler_fuel_and_power_checks(tmp_path, connected):
    backend, _ = progress_scenario()
    backend.state.factory['entities']['utility:boiler'] = machine(
        'boiler', unit_number=901, fuel={'coal': 1})
    if connected:
        connected_power_plant(backend.state)
    loop = controller(backend, tmp_path, kind=RouteLoop)
    plans, _ = loop._compile_candidates(backend.state)
    assert plans
    step = plans[0].steps[0]
    # Preparing a pack for an idle lab does not burn boiler fuel. The lab's
    # current science deficit is resolved before the power check is allowed
    # to request that service.
    assert step.action == 'factory_insert'
    assert step.parameters['role'] == 'utility:lab'
    assert step.parameters['item'] == 'logistic-science-pack'

    backend.state.factory['entities']['utility:lab']['input'] = {
        'logistic-science-pack': 20}
    plans, _ = loop._compile_candidates(backend.state)
    assert plans
    step = plans[0].steps[0]
    if connected:
        assert step.action == 'factory_insert'
        assert step.parameters['role'] == 'utility:boiler'
        assert step.parameters['item'] == 'coal'
        assert step.parameters['quantity'] == 4
    else:
        assert step.parameters.get('role') != 'utility:boiler'
        assert plans[0].materials['utility_power_prerequisite']['consumer_role'] == 'utility:lab'


def test_fault_retains_execution_barrier_and_failure_history(tmp_path):
    backend, _ = progress_scenario()
    loop = controller(backend, tmp_path, kind=RouteLoop)
    loop.memory.failures['historical-project'] = 2
    row(backend.state)['state'] = 'fault'
    record = loop.step()
    assert record['status'] == 'uncertain'
    assert backend.calls == []
    assert loop.memory.failures['historical-project'] == 2
