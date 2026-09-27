"""Coal/forecast integration: modeled Lua stock is not Factorio acceptance."""
from copy import deepcopy

import pytest

from jev_factorio import coal_supply as coal
from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.factory import FactoryPlanner
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from coal_supply_fixtures import snapshot, plain
from test_coal_supply_lua import runtime
from test_solid_route_integration import native_catalog


def stocked_network(*, target='alpha', amount=20):
    lua = runtime()
    lua.execute('coal_all()')
    # Modeled mined stock remains inside its dedicated chest; it has not been
    # delivered or made available for manual service of another consumer.
    ore = 'ore1' if target == 'alpha' else 'ore2'
    lua.execute(f'{ore}.amount={ore}.amount-{amount};'
                f'campaign.entities["coal:{target}:chest"].output.values.coal={amount};'
                'stock.coal=0;game.tick=game.tick+60')
    state = snapshot(lua)
    # The small shared Lua observer double omits container output, which the
    # real lua/factory.lua observes. Populate that field from the SAME modeled
    # inventory, not a second fabricated stock count.
    for consumer in ('alpha', 'beta'):
        source = coal.role(consumer, 'chest')
        state.factory['entities'][source]['output'] = plain(
            lua.eval(f'campaign.entities["{source}"].output.values'))
    assert state.factory['entities'][coal.role(target, 'chest')]['output']['coal'] == amount
    state.factory['fair_resource_targets'] = {
        'coal': {'name': 'coal', 'surface_index': 1, 'position': {'x': -40, 'y': -40}}}
    state.nearby_resources['coal'] = 100
    return state


@pytest.mark.parametrize('target', ['alpha', 'beta'])
@pytest.mark.parametrize('amount', [1, 20, 80])
def test_source_stock_cannot_be_general_collectible_or_forecast_supply(target, amount):
    state = stocked_network(target=target, amount=amount)
    before = deepcopy(state)
    params = {'role': coal.role(target, 'chest'), 'item': 'coal', 'quantity': 1,
              'receipt': 'isolation:extract'}
    assert not coal.permits('factory_extract', params, state)
    ledger = SupplyLedger.capture(state, native_catalog())
    assert ledger.collectible.get('coal', 0) == 0
    assert ledger.forecast_stock().get('coal', 0) == 0
    assert state == before


@pytest.mark.parametrize('kind', [FactoryPlanner, ReadyWorkPlanner])
@pytest.mark.parametrize('target', ['alpha', 'beta'])
def test_manual_service_gathers_instead_of_selecting_a_forbidden_single_pickup(kind, target):
    state = stocked_network(target=target)
    plan = kind(native_catalog(), state, 'iron_smelting')._need('coal', 8)
    assert plan.steps[0].action == 'factory_gather'
    assert plan.steps[0].parameters == {'resource': 'coal', 'quantity': 8}


def test_ready_work_horizon_does_not_treat_dedicated_source_as_paid_general_supply():
    state = stocked_network()
    planner = ReadyWorkPlanner(native_catalog(), state, 'iron_smelting')
    planner._set_focus('coal', 8)
    assert planner.raw_targets.get('coal') == 8


@pytest.mark.parametrize('alias', [False, True])
def test_private_source_alias_cannot_escape_the_forecast_or_pickup_filter(alias):
    state = stocked_network()
    if alias:
        state.factory['entities']['a-public-alias'] = deepcopy(
            state.factory['entities']['coal:alpha:chest'])
    # The controller additionally rejects aliased ownership. Even the pure
    # forecast/pickup helpers must not relabel that inventory as public supply.
    ledger = SupplyLedger.capture(state, native_catalog())
    assert ledger.collectible.get('coal', 0) == 0
    plan = FactoryPlanner(native_catalog(), state, 'iron_smelting')._need('coal', 8)
    assert plan.steps[0].action == 'factory_gather'


def test_unrelated_collectible_stock_is_retained_without_touching_coal_ownership():
    state = stocked_network()
    state.factory['entities']['public:coal'] = {
        'name': 'wooden-chest', 'unit_number': 9876,
        'position': {'x': -30, 'y': -30}, 'output': {'coal': 7}}
    before = deepcopy(state.factory['coal_supply'])
    ledger = SupplyLedger.capture(state, native_catalog())
    assert ledger.collectible.get('coal') == 7
    plan = FactoryPlanner(native_catalog(), state, 'iron_smelting')._need('coal', 8)
    assert plan.steps[0].action == 'factory_extract'
    assert plan.steps[0].parameters['role'] == 'public:coal'
    assert plan.steps[0].parameters['quantity'] == 7
    assert state.factory['coal_supply'] == before


def test_stale_coal_payload_does_not_silently_fall_back_to_public_inventory():
    state = stocked_network()
    state.factory['coal_supply']['tick'] -= 1
    with pytest.raises(ValueError):
        SupplyLedger.capture(state, native_catalog())
    with pytest.raises(ValueError):
        FactoryPlanner(native_catalog(), state, 'iron_smelting')._output_pickup('coal', 8)


def test_batch_wait_fallback_does_not_reintroduce_private_source_pickup():
    state = stocked_network()
    state.factory['entities']['public:coal'] = {
        'name': 'wooden-chest', 'unit_number': 9876,
        'position': {'x': -30, 'y': -30}, 'output': {'coal': 1}}

    class WaitingProducer(ReadyWorkPlanner):
        # Exercise the real alternative-source scan after an ordinary source
        # has been selected and batched into a wait. The fake batching policy
        # isolates this branch; it is not a claim about a native coal recipe.
        def _output_pickup(self, item, missing):
            return self._transfer('public:coal', item, 1, extracting=True)

        def _batch_collection(self, plan, amount):
            if plan.steps[0].parameters['role'] == 'public:coal':
                return self._wait('machine_output', 'coal', amount, 'public:coal')
            return super()._batch_collection(plan, amount)

    plan = WaitingProducer(native_catalog(), state, 'iron_smelting')._need('coal', 8)
    assert plan.steps[0].action == 'factory_wait'


def test_output_filter_is_recomputed_after_a_changed_observation():
    state = stocked_network()
    planner = FactoryPlanner(native_catalog(), state, 'iron_smelting')
    assert planner._output_pickup('coal', 8) is None
    state.factory['entities']['public:coal'] = {
        'name': 'wooden-chest', 'unit_number': 9876,
        'position': {'x': -30, 'y': -30}, 'output': {'coal': 3}}
    assert planner._output_pickup('coal', 8).steps[0].parameters['role'] == 'public:coal'
    # No new model/provider call or explicit cache invalidation is needed.
    state.factory['entities'].pop('public:coal')
    assert planner._output_pickup('coal', 8) is None


def test_no_coal_extension_preserves_ordinary_collectible_inventory():
    from test_factory import snapshot as ordinary, catalog
    state = ordinary()
    state.factory['entities']['coal:legacy:chest'] = {
        'unit_number': 9001, 'name': 'wooden-chest',
        'position': {'x': 1, 'y': 1}, 'output': {'coal': 6}}
    assert SupplyLedger.capture(state, catalog()).collectible['coal'] == 6
    assert FactoryPlanner(catalog(), state, 'iron_smelting')._need('coal', 6).steps[0].action == 'factory_extract'


@pytest.mark.parametrize('lost_ack', [False, True])
def test_composed_controller_fuels_unserved_demand_without_unlocking_the_network(tmp_path, lost_ack):
    """Build through the controller, then gather with its real pending verifier."""
    import json
    from jev_factorio.coal_controller import coal_loop_type
    from jev_factorio.solid_controller import solid_loop_type
    from test_coal_supply_integration import Backend, controller
    from test_solid_route_integration import FoundationScenario

    class NeedsManualFuel(FoundationScenario):
        needs_manual = False

        def _compile_candidates(self, current):
            if not self.needs_manual:
                return super()._compile_candidates(current)
            plan = ReadyWorkPlanner(self.catalog, current, self.memory.active_goal)._need('coal', 8)
            return ([plan] if plan else []), ''

    class GatheringBackend(Backend):
        def execute(self, action, parameters):
            if action != 'factory_gather':
                return super().execute(action, parameters)
            saved = json.loads(self.checkpoint.read_text())
            assert saved['pending']['action'] == action
            assert saved['active_plan']['steps'][0]['parameters'] == parameters
            self.calls.append((action, deepcopy(parameters)))
            self.state.inventory['coal'] += parameters['quantity']
            if lost_ack:
                raise TimeoutError('modeled gather acknowledgement loss')
            return 'modeled sequential gather'

    kind = coal_loop_type(solid_loop_type(NeedsManualFuel))
    backend = GatheringBackend()
    loop = controller(backend, tmp_path, kind=kind)
    count = sum(2 + len(row['corridor']) for row in coal.sources(backend.state).values())
    for _ in range(count):
        assert loop.step()['verified']
    loop.needs_manual = True
    backend.state.inventory['coal'] = 0
    backend.state.factory['entities']['coal:alpha:chest']['output'] = {'coal': 20}
    backend.state.factory['fair_resource_targets'] = {
        'coal': {'name': 'coal', 'surface_index': 1, 'position': {'x': -40, 'y': -40}}}
    before = deepcopy(loop.memory.coal_commitments)
    record = loop.step()
    assert record['action'] == 'factory_gather', record
    if lost_ack:
        resumed = controller(backend, tmp_path, kind=kind, resume=True)
        resumed.needs_manual = True
        record = resumed.step()
        loop = resumed
    assert record['verified'], record
    assert backend.calls[-1] == ('factory_gather', {'resource': 'coal', 'quantity': 8})
    assert len(backend.calls) == count + 1  # No repeated gather after a lost reply.
    assert backend.state.inventory['coal'] == 8
    assert backend.state.factory['entities']['coal:alpha:chest']['output']['coal'] == 20
    assert loop.memory.coal_commitments == before
    assert loop.memory.pending is None
