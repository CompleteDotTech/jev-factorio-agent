"""Read-only native recipe edge; fixtures never claim stock provenance."""
from copy import deepcopy
from importlib.resources import files
import json

import pytest

import jev_factorio.downstream_recipe_witness as witness
from jev_factorio.backends.native_attachment import (
    PINNED_ASSETS, WATER_ORIGIN_OBSERVATION_PROFILE,
)
from jev_factorio.downstream_recipe_witness import (
    CAPTURE_SCHEMA, capture_bundle, command, decode, decode_capture_bundle,
    observe_capture, validate_request,
)


REQUEST = {
    'route': 'solid:1:2:iron-plate:input',
    'producer_role': 'recipe:iron-gear-wheel', 'producer_unit': 2,
    'product_item': 'iron-gear-wheel',
    'consumer_role': 'recipe:automation-science-pack', 'consumer_unit': 3,
    'science_pack': 'automation-science-pack',
}
ROUTE = {'id': REQUEST['route'], 'item': 'iron-plate',
         'source_role': 'buffer:iron-plate', 'source_unit': 1,
         'target_role': REQUEST['producer_role'], 'target_unit': 2, 'paid_parts': 2}
EPOCH = {'session_id': 'witness-session', 'actor_index': 1, 'actor_unit': 9,
         'surface_index': 1, 'force_index': 1, 'min_tick': 100, 'max_tick': 110}
ATTACHMENT = {'session_id': 'witness-session', 'actor_unit': 9,
              'modules': {'solid_routes': True},
              'native_installation': {'profile': WATER_ORIGIN_OBSERVATION_PROFILE,
                  'assets': {'solid_routes': PINNED_ASSETS['solid_routes']}}}


def result():
    return {'schema': 'jev.downstream-recipe-witness.v1', 'status': 'observed',
            'reason': 'none', 'base_version': '2.0.77',
            'epoch': {'session_id': 'witness-session', 'tick': 105,
                      'actor_index': 1, 'actor_unit': 9,
                      'surface_index': 1, 'force_index': 1},
            'request': dict(REQUEST), 'route': dict(ROUTE),
            'producer': {'role': REQUEST['producer_role'], 'unit': 2,
                'recipe': {'name': 'iron-gear-wheel', 'product': 'iron-gear-wheel',
                           'product_amount': 1, 'ingredients': {'iron-plate': 2}}},
            'consumer': {'role': REQUEST['consumer_role'], 'unit': 3,
                'recipe': {'name': 'automation-science-pack',
                           'product': 'automation-science-pack',
                           'product_amount': 1,
                           'ingredients': {'iron-gear-wheel': 1, 'copper-plate': 1}}},
            'recipe_dependency_verified': True,
            'stock_provenance_qualified': False, 'mutation_authorized': False}


def check(row):
    return decode(row, request=REQUEST, expected_epoch=EPOCH,
                  expected_route=ROUTE, attachment=ATTACHMENT)


def test_native_shaped_recipe_edge_is_dependency_only():
    row = result()
    assert check(row)['recipe_dependency_verified'] is True
    assert row['stock_provenance_qualified'] is False
    assert row['mutation_authorized'] is False
    source = command(REQUEST, ATTACHMENT)
    assert source.startswith('/sc local request=helpers.json_to_table(')
    assert '-- Fixed read-only recipe dependency witness.' in source


def test_capture_bundle_is_replayable_and_strips_unneeded_attachment_fields():
    attachment = deepcopy(ATTACHMENT)
    attachment.update(qualified=True, coal_targets=[{'private': 'do not retain'}],
                      connector_snapshot_ownership={'private': 'do not retain'})
    bundle = capture_bundle(result(), request=REQUEST, expected_epoch=EPOCH,
                            expected_route=ROUTE, attachment=attachment)
    assert bundle['schema'] == CAPTURE_SCHEMA
    assert set(bundle) == {'schema', 'request', 'expected_epoch', 'expected_route',
                           'attachment', 'result'}
    assert bundle['attachment'] == ATTACHMENT
    assert 'coal_targets' not in bundle['attachment']
    assert decode_capture_bundle(bundle, request=REQUEST, expected_epoch=EPOCH,
                                 expected_route=ROUTE)['recipe_dependency_verified'] is True

    changed = deepcopy(bundle)
    changed['result']['consumer']['recipe']['ingredients'].pop('iron-gear-wheel')
    with pytest.raises(ValueError):
        decode_capture_bundle(changed, request=REQUEST, expected_epoch=EPOCH,
                              expected_route=ROUTE)

    with pytest.raises(ValueError, match='invalid_capture_binding'):
        decode_capture_bundle(bundle, request={**REQUEST, 'consumer_unit': 99},
                              expected_epoch=EPOCH, expected_route=ROUTE)


@pytest.mark.parametrize('change', [
    lambda r: r['epoch'].update(tick=111),
    lambda r: r['epoch'].update(actor_unit=999),
    lambda r: r['route'].update(target_unit=999),
    lambda r: r['route'].update(paid_parts=0),
    lambda r: r['producer']['recipe']['ingredients'].pop('iron-plate'),
    lambda r: r['consumer']['recipe']['ingredients'].pop('iron-gear-wheel'),
    lambda r: r.update(stock_provenance_qualified=True),
    lambda r: r.update(mutation_authorized=True),
    lambda r: r.update(base_version='2.1.19'),
    lambda r: r['consumer']['recipe'].update(product_amount=0),
    lambda r: r['consumer']['recipe'].update(product_amount=2.5),
    lambda r: r['producer']['recipe'].update(ingredients={'iron-plate': True}),
])
def test_changed_epoch_binding_bill_or_authority_fails_closed(change):
    row = result()
    change(row)
    with pytest.raises(ValueError):
        check(row)


def test_unqualified_native_version_or_missing_asset_never_qualifies():
    row = result()
    row.update(status='unqualified', reason='native_version',
               recipe_dependency_verified=False, route={}, producer={}, consumer={})
    assert check(row)['recipe_dependency_verified'] is False
    row['base_version'] = '2.1.19'
    with pytest.raises(ValueError):
        check(row)
    attachment = deepcopy(ATTACHMENT)
    attachment['modules']['solid_routes'] = False
    with pytest.raises(RuntimeError):
        decode(result(), request=REQUEST, expected_epoch=EPOCH,
               expected_route=ROUTE, attachment=attachment)
    attachment = deepcopy(ATTACHMENT)
    attachment['native_installation']['profile'] = 'e759-observation-v2-expanded-oil-v3'
    with pytest.raises(ValueError, match='invalid_attachment'):
        command(REQUEST, attachment)


def test_deterministic_multi_pack_science_output_is_a_valid_recipe_edge():
    row = result()
    row['consumer']['recipe']['product_amount'] = 2
    assert check(row)['recipe_dependency_verified'] is True


def test_request_is_bounded_data_not_lua_source():
    bad = {**REQUEST, 'route': 'solid:1); os.execute("bad")'}
    with pytest.raises(ValueError, match='invalid_request'):
        validate_request(bad)
    with pytest.raises(ValueError):
        command(bad, ATTACHMENT)


def test_observer_qualifies_attachment_before_one_ordered_read_only_query(monkeypatch):
    calls = []
    def qualified(client, *, receipt_path=None):
        calls.append('readback')
        return deepcopy(ATTACHMENT)
    class Client:
        def send_command(self, source):
            calls.append('query')
            assert source == command(REQUEST, ATTACHMENT)
            return json.dumps(result())
    monkeypatch.setattr(witness, 'readback', qualified)
    row = witness.observe(Client(), request=REQUEST, expected_epoch=EPOCH,
                          expected_route=ROUTE)
    assert row['recipe_dependency_verified'] is True
    assert calls == ['readback', 'query']


def test_observer_capture_keeps_the_same_ordered_readback_and_query(monkeypatch):
    calls = []
    attachment = deepcopy(ATTACHMENT)
    attachment.update(qualified=True, solid_intents=[{'private': 'do not retain'}])

    def qualified(client, *, receipt_path=None):
        calls.append('readback')
        return deepcopy(attachment)

    class Client:
        def send_command(self, source):
            calls.append('query')
            assert source == command(REQUEST, attachment)
            return json.dumps(result())

    monkeypatch.setattr(witness, 'readback', qualified)
    bundle = observe_capture(Client(), request=REQUEST, expected_epoch=EPOCH,
                             expected_route=ROUTE)
    assert decode_capture_bundle(bundle, request=REQUEST, expected_epoch=EPOCH,
                                 expected_route=ROUTE)['recipe_dependency_verified'] is True
    assert 'solid_intents' not in bundle['attachment']
    assert calls == ['readback', 'query']


LUA = files('jev_factorio').joinpath('lua/downstream_recipe_witness.lua').read_text()
LUA_FIXTURE = r'''
script={active_mods={base="2.0.77"}}
local surface,force={index=1},{index=1}
local actor={valid=true,unit_number=9,surface=surface,force=force}
local player={connected=true,character=actor,cheat_mode=false}
game={tick=105,speed=1,tick_paused=false,connected_players={player},
    get_player=function(index) assert(index==1);return player end}
local function entity(unit,recipe)
    return {valid=true,unit_number=unit,surface=surface,force=force,
        quality={name="normal"},type="assembling-machine",productivity_bonus=0,
        get_module_inventory=function() return nil end,
        get_recipe=function() return recipe end}
end
source=entity(1,{})
producer=entity(2,{name="iron-gear-wheel",
    ingredients={{type="item",name="iron-plate",amount=2}},
    products={{type="item",name="iron-gear-wheel",amount=1}}})
consumer=entity(3,{name="automation-science-pack",
    ingredients={{type="item",name="iron-gear-wheel",amount=1},
                 {type="item",name="copper-plate",amount=1}},
    products={{type="item",name="automation-science-pack",amount=1}}})
part1={valid=true,unit_number=4,surface=surface,force=force}
part2={valid=true,unit_number=5,surface=surface,force=force}
local route="solid:1:2:iron-plate:input"
local cells={}
cells[route]={route=route,item="iron-plate",source={role="buffer:iron-plate",unit_number=1},
    target={role="recipe:iron-gear-wheel",unit_number=2,inventory="input",recipe="iron-gear-wheel"},
    steps={{part="receive"},{part="send"}},
    parts={receive={paid=1,receipt="receipt-1",entity=part1,role="route:receive",unit_number=4},
           send={paid=1,receipt="receipt-2",entity=part2,role="route:send",unit_number=5}}}
jev_fle_runtime={jev_session_id="witness-session",jev_bound_player_index=1,
    agent_characters={[1]=actor},
    campaign={entities={["buffer:iron-plate"]=source,["recipe:iron-gear-wheel"]=producer,
        ["recipe:automation-science-pack"]=consumer,["route:receive"]=part1,["route:send"]=part2}},
    solid_routes={protocol=1,implementation_revision=4,
        contract_family="straight-solid-corridor-v1",cells=cells},
    native_installation={profile="e759-observation-v2-water-origin-v4",
        assets={solid_routes="88b8f605e439a1f16783b9f9cc1e222f002f6be6e9b27494dbc309dce55b5809"}}}
request={route=route,producer_role="recipe:iron-gear-wheel",producer_unit=2,
    product_item="iron-gear-wheel",consumer_role="recipe:automation-science-pack",
    consumer_unit=3,science_pack="automation-science-pack"}
helpers={table_to_json=function(v) return v end}
rcon={print=function(v) output=v end}
'''


@pytest.mark.parametrize('change,expected', [
    ('', 'observed'),
    ('consumer.get_recipe().products[1].amount=2', 'observed'),
    ('producer.get_recipe().products[1].probability=.5', 'unqualified'),
    ('consumer.get_recipe().products[1].amount_min=1', 'unqualified'),
    ('consumer.get_recipe().products[1].quality_change=1', 'unqualified'),
    ('consumer.get_recipe().products[1].type="fluid"', 'unqualified'),
    ('consumer.get_recipe().ingredients[1].name="stone"', 'unqualified'),
    ('jev_fle_runtime.campaign.entities["recipe:iron-gear-wheel"]=consumer', 'unqualified'),
    ('jev_fle_runtime.solid_routes.cells[request.route].parts.send=nil', 'unqualified'),
    ('jev_fle_runtime.solid_routes.cells[request.route].steps[2].part="receive"', 'unqualified'),
    ('jev_fle_runtime.solid_routes.cells[request.route].parts.send.receipt="receipt-1"', 'unqualified'),
    ('script.active_mods.base="2.1.19"', 'unqualified'),
])
def test_fixed_lua_observes_only_deterministic_owned_recipe_edge(change, expected):
    lua52 = pytest.importorskip('lupa.lua52')
    lua = lua52.LuaRuntime()
    lua.execute(LUA_FIXTURE)
    if change:
        lua.execute(change)
    lua.execute(LUA)
    assert lua.globals().output.status == expected
    assert lua.globals().output.recipe_dependency_verified is (expected == 'observed')
    assert lua.globals().output.stock_provenance_qualified is False
    assert lua.globals().output.mutation_authorized is False
    if expected == 'observed':
        def plain(value):
            if hasattr(value, 'items'):
                return {key: plain(item) for key, item in value.items()}
            return value
        assert check(plain(lua.globals().output))['recipe_dependency_verified'] is True
        if change:
            assert lua.globals().output.consumer.recipe.product_amount == 2
