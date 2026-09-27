"""General planning cannot spend or pick up exclusively owned network coal.

Real Lua contract with modeled mining/inventories; no Factorio-engine evidence.
"""
from copy import deepcopy
import pytest

from jev_factorio import coal_supply as coal
from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.factory import FactoryPlanner
from coal_supply_fixtures import snapshot
from test_coal_mixed_transport import native
from test_solid_route_integration import native_catalog


def buffered():
    lua = native()
    lua.execute('''
        coal_all(); stock.coal=0
        ore1.amount=ore1.amount-5
        campaign.entities["coal:alpha:chest"].output.values.coal=5
        game.tick=game.tick+60;result=campaign.observe()
        assert(result.coal_supply.sources.alpha.state=="ready")
    ''')
    state = snapshot(lua)
    assert all(coal.current(row, state) for row in coal.sources(state).values())
    state.nearby_resources['coal'] = 100
    state.factory['fair_resource_targets'] = {'coal': {'name':'coal', 'surface_index':1,
                                                    'position':{'x':100,'y':100}}}
    return state, native_catalog()


def test_general_ledger_does_not_credit_the_networks_exclusive_coal_buffer():
    state, catalog = buffered()
    before = deepcopy(state)
    # The current wire deliberately keeps source stock out of generic output.
    assert 'output' not in state.factory['entities']['coal:alpha:chest']
    ledger = SupplyLedger.capture(state, catalog)
    assert ledger.collectible.get('coal', 0) == 0
    assert ledger.forecast_stock().get('coal', 0) == 0
    assert state == before


def test_manual_coal_need_retains_executable_gather_instead_of_forbidden_pickup():
    state, catalog = buffered()
    plan = FactoryPlanner(catalog, state, 'iron_smelting')._need('coal', 10)
    step = plan.steps[0]
    assert step.action == 'factory_gather', plan
    assert coal.permits(step.action, step.parameters, state)
    assert step.parameters['quantity'] == 10


def test_unrelated_owned_coal_output_remains_collectible():
    state, catalog = buffered()
    state.factory['entities']['manual:coal'] = dict(name='wooden-chest',unit_number=99999,
        position={'x':80,'y':80},output={'coal':3})
    assert SupplyLedger.capture(state,catalog).collectible['coal'] == 3
    plan = FactoryPlanner(catalog,state,'iron_smelting')._need('coal',10)
    assert plan.steps[0].action == 'factory_extract'
    assert plan.steps[0].parameters['role'] == 'manual:coal'


@pytest.mark.parametrize('alias', ['alias:source', 'aaa:source'])
def test_alias_cannot_launder_reserved_coal_into_actor_forecast_or_pickup(alias):
    state, catalog = buffered()
    state.factory['entities'][alias] = deepcopy(state.factory['entities']['coal:alpha:chest'])
    assert SupplyLedger.capture(state,catalog).collectible.get('coal',0) == 0
    plan = FactoryPlanner(catalog,state,'iron_smelting')._need('coal',10)
    assert plan.steps[0].action == 'factory_gather'
    # Alias ambiguity still fails the separate authoritative native-current guard.
    assert not coal.permits(plan.steps[0].action,plan.steps[0].parameters,state)


def test_non_coal_extension_workload_has_unchanged_general_supply():
    from solid_routes_fixtures import fixture
    state = fixture(); catalog = native_catalog()
    state.factory['entities']['normal:chest'] = dict(unit_number=9999,output={'coal':3})
    assert SupplyLedger.capture(state,catalog).collectible['coal'] == 3
    plan = FactoryPlanner(catalog,state,'iron_smelting')._output_pickup('coal',10)
    assert plan.steps[0].parameters['role'] == 'normal:chest'
