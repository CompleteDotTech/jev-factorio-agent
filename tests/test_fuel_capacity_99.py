"""Read-only native capacity hints and bounded manual service, not flow proof."""
from copy import deepcopy
import json

import pytest

from test_atomic_observation import setup as atomic_setup
from test_atomic_observation_lua import runtime, converted
from test_fuel_service_bounds import direct
from test_grouped_fuel_service import due_scenario


@pytest.mark.parametrize('capacity,wanted', [(0, 4), (1, 5), (4, 8), (100, 8)])
def test_optional_consumer_capacity_caps_acquisition(capacity, wanted):
    state, data = due_scenario()
    state.factory['entities']['input:drill']['fuel_insertable'] = {'coal': capacity}
    result = direct(state, data)
    assert result.steps[0].parameters['quantity'] == wanted


def test_full_primary_does_not_gather_coal_it_cannot_receive():
    state, data = due_scenario()
    state.factory['entities']['input:inserter']['fuel_insertable'] = {'coal': 0}
    with pytest.raises(ValueError, match='primary.*capacity'):
        direct(state, data)


def test_partial_primary_transfer_is_capped_but_leaves_other_coal_held():
    state, data = due_scenario(8)
    state.factory['entities']['input:inserter']['fuel_insertable'] = {'coal': 2}
    result = direct(state, data)
    assert result.steps[0].action == 'factory_insert'
    assert result.steps[0].parameters['quantity'] == 2
    assert result.materials['fuel_service']['combined_deficit'] == 6


@pytest.mark.parametrize('field', ['inventory_insertable', 'fuel_insertable'])
@pytest.mark.parametrize('invalid', [-1, True, 1.5, float('nan'), float('inf'), 2**53, '4'])
def test_invalid_capacity_cannot_authorize_service(field, invalid):
    state, data = due_scenario()
    target = state.factory if field == 'inventory_insertable' else state.factory['entities']['input:inserter']
    target[field] = {'coal': invalid}
    with pytest.raises(ValueError):
        direct(state, data)


def test_consumer_capacity_alias_conflict_is_rejected():
    state, data = due_scenario()
    primary = state.factory['entities']['input:inserter']
    primary['fuel_insertable'] = {'coal': 4}
    state.factory['entities']['alias'] = deepcopy(primary)
    state.factory['entities']['alias']['fuel_insertable'] = {'coal': 1}
    state.factory['input_routes']['sources']['recipe:iron-plate']['parts']['drill']['role'] = 'alias'
    with pytest.raises(ValueError, match='Aliased'):
        direct(state, data)


def test_changed_capacity_replans_without_reusing_permission():
    state, data = due_scenario()
    consumer = state.factory['entities']['input:drill']
    consumer['fuel_insertable'] = {'coal': 1}
    assert direct(state, data).steps[0].parameters['quantity'] == 5
    consumer['fuel_insertable'] = {'coal': 0}
    state.tick += 1
    for key in ('output_buffers', 'input_routes'):
        state.factory[key]['tick'] = state.tick
    assert direct(state, data).steps[0].parameters['quantity'] == 4


SETUP = '''
main_capacity,burner_capacity,main_capacity_calls,burner_capacity_calls=3,2,0,0
main_inventory={valid=true,get_contents=function() return actor_items end,
    get_insertable_count=function(item)
        assert(item.name=="coal" and item.quality=="normal")
        main_capacity_calls=main_capacity_calls+1;return main_capacity end}
player.get_main_inventory=function() return main_inventory end
burner_inventory={valid=true,get_insertable_count=function(item)
    assert(item.name=="coal" and item.quality=="normal")
    burner_capacity_calls=burner_capacity_calls+1;return burner_capacity end}
owned={name="burner-inserter",unit_number=900,valid=true,force=force,surface=surface,
    get_fuel_inventory=function() return burner_inventory end}
storage.campaign.entities={burner=owned,alias=owned}
local previous=storage.campaign.observe
storage.campaign.observe=function()
    local value=previous()
    value.entities={burner={name="burner-inserter",unit_number=900,fuel={coal=1}},
                    alias={name="burner-inserter",unit_number=900,fuel={coal=1}}}
    return value
end
'''


def capacity_runtime():
    lua = runtime()
    lua.execute(SETUP)
    return lua


def test_lua_observation_samples_actor_and_deduplicated_owned_consumer():
    lua = capacity_runtime()
    lua.execute('''storage.campaign.observation_snapshot_v2(0)
        assert(captured.factory.inventory_insertable.coal==3)
        assert(captured.factory.entities.burner.fuel_insertable.coal==2)
        assert(captured.factory.entities.alias.fuel_insertable.coal==2)
        assert(main_capacity_calls==1 and burner_capacity_calls==1)
        assert(campaign_count==1 and control_count==1 and discovery_count==5)''')


def test_lua_capacity_is_fresh_and_zero_is_known_not_unknown():
    lua = capacity_runtime()
    lua.execute('''storage.campaign.observation_snapshot_v2(0)
        main_capacity=0;burner_capacity=0;game.tick=11;storage.campaign.observation_snapshot_v2(0)
        assert(captured.factory.inventory_insertable.coal==0)
        assert(captured.factory.entities.burner.fuel_insertable.coal==0)
        assert(main_capacity_calls==2 and burner_capacity_calls==2)''')


@pytest.mark.parametrize('change', [
    'main_inventory.get_insertable_count=nil;burner_inventory.get_insertable_count=nil',
    'main_inventory.get_insertable_count=function() error("unavailable private") end;burner_inventory.get_insertable_count=nil',
])
def test_unavailable_native_getter_remains_unknown_without_stale_hint(change):
    lua = capacity_runtime()
    lua.execute('storage.campaign.observation_snapshot_v2(0);' + change + ';game.tick=11;storage.campaign.observation_snapshot_v2(0)')
    value = converted(lua.globals().captured)['factory']
    assert 'inventory_insertable' not in value
    assert 'fuel_insertable' not in value['entities']['burner']
    assert 'private' not in json.dumps(value)


@pytest.mark.parametrize('change', [
    'main_capacity=-1', 'main_capacity=1.5', 'burner_capacity=-1', 'burner_capacity=1.5',
])
def test_bad_native_capacity_has_no_usable_payload(change):
    lua = capacity_runtime()
    lua.execute(change + ';assert(not pcall(storage.campaign.observation_snapshot_v2,0));assert(captured==nil)')


@pytest.mark.parametrize('change', ['owned.unit_number=901', 'owned.force={index=99}', 'owned.surface={index=99}', 'owned.valid=false'])
def test_replaced_or_foreign_native_entity_cannot_supply_capacity(change):
    lua = capacity_runtime()
    lua.execute(change + ';storage.campaign.observation_snapshot_v2(0)')
    value = converted(lua.globals().captured)['factory']
    assert 'fuel_insertable' not in value['entities']['burner']
    assert lua.globals().burner_capacity_calls == 0


@pytest.mark.parametrize('invalid', [True, -1, 1.5, '3'])
def test_atomic_python_decoder_rejects_malformed_capacity(monkeypatch, invalid):
    backend, _, payload, calls = atomic_setup(monkeypatch)
    payload['factory']['inventory_insertable'] = {'coal': invalid}
    with pytest.raises(ValueError, match='capacity'):
        backend.observe()
    assert len(calls) == 1


def test_native_consumer_capacity_read_budget_is_fixed_and_unknowns_not_fabricated():
    lua = capacity_runtime()
    lua.execute('''local previous=storage.campaign.observe
        storage.campaign.observe=function()
            local result=previous();result.entities={};storage.campaign.entities={}
            for i=1,17 do
                local role=string.format("burner:%02d",i)
                result.entities[role]={name="burner-inserter",unit_number=1000+i}
                storage.campaign.entities[role]={name="burner-inserter",unit_number=1000+i,
                    valid=true,surface=surface,force=force,get_fuel_inventory=function() return burner_inventory end}
            end
            return result
        end
        storage.campaign.observation_snapshot_v2(0)
        assert(burner_capacity_calls==16 and main_capacity_calls==1)
        assert(captured.factory.entities["burner:16"].fuel_insertable.coal==2)
        assert(captured.factory.entities["burner:17"].fuel_insertable==nil)''')


@pytest.mark.parametrize('field', ['inventory_insertable', 'fuel_insertable'])
def test_empty_wire_capacity_maps_remain_unknown(monkeypatch, field):
    backend, _, payload, calls = atomic_setup(monkeypatch)
    if field == 'inventory_insertable':
        payload['factory'][field] = []
    else:
        payload['factory']['entities'] = {'burner': {field: []}}
    state = backend.observe()
    target = state.factory if field == 'inventory_insertable' else state.factory['entities']['burner']
    assert target[field] == {} and len(calls) == 1


@pytest.mark.parametrize('capacity', [None, {}])
def test_unknown_hint_keeps_existing_bounded_service(capacity):
    state, data = due_scenario()
    state.factory['entities']['input:drill']['fuel_insertable'] = capacity
    state.factory['inventory_insertable'] = capacity
    plan = direct(state, data)
    assert plan.steps[0].parameters['quantity'] == 8
    assert plan.materials['fuel_service']['inventory_insertable'] is None


def test_full_actor_capacity_still_allows_spendable_carried_coal_transfer():
    state, data = due_scenario(1)
    state.factory['inventory_insertable'] = {'coal': 0}
    plan = direct(state, data)
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters['quantity'] == 1
