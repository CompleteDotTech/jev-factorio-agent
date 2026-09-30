"""The v7 query counts current alternatives but never claims future terrain closed."""
from copy import deepcopy

import pytest

from jev_factorio.coal_economic_observation import UNIT_QUALIFICATION
from jev_factorio.coal_economic_v7 import (
    PROFILE, SCHEMA, alternative_stock_upper_bounds, decode_v7, fixed_query,
    query_sha256,
)
from jev_factorio.backends.native_attachment import (
    MANUAL_CYCLE_PROFILE, manual_journal_sha256,
)
from test_coal_economic_v6 import _installed_v6_mock
from test_coal_native_evidence import example, lua_runtime, plain


def projected(*, alternatives=False, force_mining_modifier=0,
              character_mining_modifier=0, legacy_v1_history=False):
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    _installed_v6_mock(lua)
    if legacy_v1_history:
        lua.execute('''
            local rt=jev_fle_runtime
            rt.coal_manual_cycle_v2=nil
            rt.native_installation.profile=MANUAL_PROFILE
            rt.native_installation.assets.coal_manual_cycle_v2=nil
            rt.native_installation.callbacks.cycle_tick=nil
            local journal=rt.coal_manual_journal_v1
            local gather={receipt='gather-legacy',status='complete',pending=false,
                overflow=false,fault=false,session_id=rt.jev_session_id,actor_index=1,
                actor_unit=actor.unit_number,surface_index=surface.index,
                force_index=force.index,resource='coal',started_tick=9000,
                finished_tick=9100,coal_before=0,coal_after=5,
                walking_ticks=20,mining_ticks=30}
            journal.order={'gather-legacy'}
            journal.rows={['gather-legacy']=gather}
            campaign.receipt_order={'deliver-legacy-1','deliver-legacy-2'}
            campaign.receipts={
                ['deliver-legacy-1']={role='furnace',item='coal',quantity=2,
                    unit_number=campaign.entities.furnace.unit_number,
                    extracting=false,tick=9200},
                ['deliver-legacy-2']={role='utility:boiler',item='coal',quantity=3,
                    unit_number=campaign.entities['utility:boiler'].unit_number,
                    extracting=false,tick=9300}}
        '''.replace('MANUAL_PROFILE', repr(MANUAL_CYCLE_PROFILE)))
    lua.execute('''
        -- The native LuaEntity character always has a position. The shared
        -- economics-query double intentionally predates the v7 actor-position
        -- census, so supply a valid Factorio-shaped position here.
        actor.position={x=100.5,y=200.5}
        actor.prototype={mining_speed=0.5}
        force.manual_mining_speed_modifier=0
        player.character_mining_speed_modifier=0
        local old_search=surface.find_entities_filtered
        surface.find_entities_filtered=function(options)
            if options.area and (options.type=='item-entity' or options.type=='tree') then
                local source=options.type=='item-entity' and (ground_entities or {})
                    or (tree_entities or {})
                local result={}
                for _,entity in ipairs(source) do
                    local p=entity.position
                    if p and p.x>=options.area.left_top.x and p.x<=options.area.right_bottom.x
                        and p.y>=options.area.left_top.y and p.y<=options.area.right_bottom.y then
                        result[#result+1]=entity
                        if #result>=options.limit then break end
                    end
                end
                return result
            end
            if options.area then return old_search(options) end
            if options.force==force then return all_entities end
            if type(options.type)=='table' then return all_entities end
            if options.type=='item-entity' then return ground_entities or {} end
            if options.type=='tree' then return tree_entities or {} end
            error('unsupported census search')
        end
        for _,e in ipairs(all_entities) do
            e.get_max_inventory_index=function() return 0 end
            e.get_inventory=function() return nil end
            e.get_fuel_inventory=e.get_fuel_inventory or function() return nil end
            e.get_output_inventory=e.get_output_inventory or function() return nil end
            e.is_crafting=e.is_crafting or function() return false end
        end
        player.get_max_inventory_index=function() return 1 end
        player.get_inventory=function() return {valid=true,get_contents=function() return {} end} end
        game.surfaces={surface}
    ''')
    lua.globals().force.manual_mining_speed_modifier = force_mining_modifier
    lua.globals().player.character_mining_speed_modifier = character_mining_modifier
    if alternatives:
        lua.execute('''
            prototypes.item.wood={fuel_value=2000000,fuel_category='chemical'}
            local ground={valid=true,type='item-entity',surface=surface,unit_number=8001,
                position={x=4.5,y=5.5},stack={valid_for_read=true,name='wood',count=3,
                    quality={name='normal'}}}
            ground_entities={ground}
            local tree={valid=true,type='tree',surface=surface,unit_number=8002,
                name='tree-01',position={x=9.5,y=2.5},
                prototype={mineable_properties={minable=true,required_fluid=nil,
                    mining_time=1,
                    products={{type='item',name='wood',amount_min=2,amount_max=5,
                        probability=0.1}}}}}
            tree_entities={tree}
        ''')
    lua.execute(fixed_query())
    return plain(lua.globals().projected), bundle


def checked(raw, bundle):
    return decode_v7(raw, expected_epoch=deepcopy(raw['epoch']),
                     expected_bundle=bundle,
                     unit_qualification=deepcopy(UNIT_QUALIFICATION),
                     expected_journal_asset_sha256=manual_journal_sha256())


def test_v7_is_a_separate_versioned_query_and_keeps_future_scope_unknown():
    raw, bundle = projected()
    assert raw['query_status'] == 'observed', raw['reason']
    assert raw['schema'] == SCHEMA
    assert len(query_sha256()) == 64
    evidence = checked(raw, bundle)
    assert evidence.profile == PROFILE
    assert evidence.alternatives.current_surface_only is True
    assert evidence.alternatives.local_scope_only is True
    assert evidence.alternatives.current_surface_coverage_complete is False
    assert evidence.alternatives.unobserved_current_surface_outside_scope is True
    assert evidence.alternatives.unobserved_chunks_inside_scope_unknown is True
    assert evidence.alternatives.future_generated_chunks_unknown is True
    assert evidence.native_payback_proven is False
    assert evidence.mutation_authorized is False


def test_v7_preserves_nonempty_v5_gather_and_paid_delivery_history_without_v6_migration():
    raw, bundle = projected(legacy_v1_history=True)
    assert raw['query_status'] == 'observed', raw['reason']
    assert raw['material_census']['native_profile'] == MANUAL_CYCLE_PROFILE
    assert raw['material_census']['cycle_asset_sha256'] is False
    assert raw['cycle_v2'] == {
        'status': 'not_installed', 'journal_asset_sha256': False,
        'cycle_complete': False, 'rows': {},
    }
    result = checked(raw, bundle)
    assert result.cycle_history_coverage == 'legacy_v1_preserved'
    assert result.cycles == ()
    assert [item.receipt for item in result.graph.manual_cycle.gathers] == ['gather-legacy']
    assert [item.receipt for item in result.graph.manual_cycle.deliveries] == [
        'deliver-legacy-1', 'deliver-legacy-2']
    assert result.native_admission_journal is None


def test_ground_and_tree_alternatives_are_counted_with_yield_upper_bounds():
    raw, bundle = projected(alternatives=True)
    assert raw['query_status'] == 'observed', raw['reason']
    census = raw['material_census']
    assert census['ground_items'] == 1 and census['trees'] == 1
    assert census['alternative_stock']['future_generated_chunks_unknown'] is True
    assert census['alternative_stock']['tree_product_upper_bounds'] == [
        {'name': 'wood', 'maximum': 5, 'fuel_value': 2_000_000,
         'fuel_category': 'chemical'}]
    evidence = checked(raw, bundle)
    assert evidence.alternatives.manual_mining_speed == 0.5
    assert evidence.alternatives.trees[0].mining_time == 1
    assert evidence.alternatives.trees[0].products[0].expected_amount == pytest.approx(0.35)
    result = alternative_stock_upper_bounds(evidence, ('coal', 'wood'))
    assert result['local_scope_counts_upper'] == {'coal': 20, 'wood': 8}
    assert result['ground_and_tree_fuel_energy_joules_upper'] == 16_000_000
    assert result['tree_ticks_per_item_estimate'] == {'wood': pytest.approx(120 / 0.35)}
    assert result['scope'] == 'actor_and_copper_consumer_radius_64'
    assert result['current_surface_coverage_complete'] is False
    assert result['observed_generated_entities_only'] is True
    assert result['unobserved_chunks_inside_scope_unknown'] is True
    assert result['unobserved_current_surface_outside_scope'] is True
    assert result['future_generated_chunks_unknown'] is True
    assert result['native_payback_proven'] is False
    assert result['mutation_authorized'] is False


def test_tree_collection_estimate_uses_actual_manual_mining_modifiers():
    raw, bundle = projected(alternatives=True, force_mining_modifier=1,
                            character_mining_modifier=1)
    evidence = checked(raw, bundle)
    result = alternative_stock_upper_bounds(evidence, ('coal', 'wood'))
    assert evidence.alternatives.manual_mining_speed == 2
    assert result['tree_ticks_per_item_estimate'] == {'wood': pytest.approx(30 / 0.35)}


def test_zero_probability_tree_product_has_no_divide_by_zero_collection_estimate():
    raw, bundle = projected(alternatives=True)
    product = raw['material_census']['alternative_stock']['trees'][0]['products'][0]
    product['probability'] = 0
    product['expected_amount'] = 0
    evidence = checked(raw, bundle)
    result = alternative_stock_upper_bounds(evidence, ('coal', 'wood'))
    assert result['tree_ticks_per_item_estimate'] == {}
    assert result['native_payback_proven'] is False


@pytest.mark.parametrize('change', [
    lambda census: census['alternative_stock'].update(future_generated_chunks_unknown=False),
    lambda census: census['alternative_stock'].update(scope='bounded_box'),
    lambda census: census['alternative_stock'].update(unobserved_current_surface_outside_scope=False),
    lambda census: census['alternative_stock'].update(unobserved_chunks_inside_scope_unknown=False),
    lambda census: census['alternative_stock'].update(tree_product_upper_bounds=[]),
    lambda census: census.update(ground_items=0),
    lambda census: census['alternative_stock']['ground_item_entities'][0].update(count=0),
    lambda census: census['alternative_stock']['trees'][0]['products'][0].update(maximum=1),
    lambda census: census['alternative_stock']['trees'][0]['products'][0].update(probability=2),
])
def test_malformed_or_incomplete_alternative_census_fails_closed(change):
    raw, bundle = projected(alternatives=True)
    change(raw['material_census'])
    with pytest.raises(ValueError):
        checked(raw, bundle)


@pytest.mark.parametrize('kind,reason', [
    ('tree-overflow', 'material_census_tree_bound'),
    ('ground-overflow', 'material_census_ground_bound'),
    ('unsupported-tree-product', 'material_census_tree_product'),
])
def test_native_alternative_query_rejects_overflow_and_unknown_yield(kind, reason):
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    _installed_v6_mock(lua)
    lua.execute('''
        actor.position={x=100.5,y=200.5}
        actor.prototype={mining_speed=0.5}
        force.manual_mining_speed_modifier=0
        player.character_mining_speed_modifier=0
        local old_search=surface.find_entities_filtered
        surface.find_entities_filtered=function(options)
            if options.area and (options.type=='item-entity' or options.type=='tree') then
                local source=options.type=='item-entity' and (ground_entities or {})
                    or (tree_entities or {})
                local result={}
                for _,entity in ipairs(source) do
                    local p=entity.position
                    if p and p.x>=options.area.left_top.x and p.x<=options.area.right_bottom.x
                        and p.y>=options.area.left_top.y and p.y<=options.area.right_bottom.y then
                        result[#result+1]=entity
                        if #result>=options.limit then break end
                    end
                end
                return result
            end
            if options.area then return old_search(options) end
            if options.force==force then return all_entities end
            if type(options.type)=='table' then return all_entities end
            if options.type=='item-entity' then return ground_entities or {} end
            if options.type=='tree' then return tree_entities or {} end
            error('unsupported census search')
        end
        for _,e in ipairs(all_entities) do
            e.get_max_inventory_index=function() return 0 end
            e.get_inventory=function() return nil end
            e.get_fuel_inventory=e.get_fuel_inventory or function() return nil end
            e.get_output_inventory=e.get_output_inventory or function() return nil end
            e.is_crafting=e.is_crafting or function() return false end
        end
        player.get_max_inventory_index=function() return 1 end
        player.get_inventory=function() return {valid=true,get_contents=function() return {} end} end
        game.surfaces={surface}
    ''')
    if kind == 'tree-overflow':
        lua.execute('tree_entities={}; for i=1,600 do tree_entities[i]={valid=true,unit_number=i,position={x=100,y=200}} end')
    elif kind == 'ground-overflow':
        lua.execute('ground_entities={}; for i=1,600 do ground_entities[i]={valid=true,unit_number=i+1000,position={x=100,y=200}} end')
    else:
        lua.execute('''
            tree_entities={{valid=true,type='tree',surface=surface,unit_number=8002,
                name='tree-01',position={x=9.5,y=2.5},prototype={mineable_properties={
                    minable=true,mining_time=1,
                    products={{type='fluid',name='water',amount=5}}}}}}
        ''')
    lua.execute(fixed_query())
    result = plain(lua.globals().projected)
    assert result['query_status'] == 'unsupported'
    assert result['reason'] == reason


def test_global_tree_overflow_outside_local_areas_does_not_claim_surface_complete():
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    _installed_v6_mock(lua)
    lua.execute('''
        actor.position={x=100.5,y=200.5}
        actor.prototype={mining_speed=.5}
        force.manual_mining_speed_modifier=0
        player.character_mining_speed_modifier=0
        tree_entities={}
        for i=1,2049 do tree_entities[i]={valid=true,type='tree',surface=surface,
            unit_number=9000+i,name='outside-tree',position={x=1000+i,y=1000},
            prototype={mineable_properties={minable=false}}} end
        ground_entities={}
        local old_search=surface.find_entities_filtered
        surface.find_entities_filtered=function(options)
            if options.area and (options.type=='item-entity' or options.type=='tree') then
                return {}
            end
            if options.area then return old_search(options) end
            if options.force==force then return all_entities end
            if type(options.type)=='table' then return all_entities end
            error('unsupported census search')
        end
        for _,e in ipairs(all_entities) do
            e.get_max_inventory_index=function() return 0 end
            e.get_inventory=function() return nil end
            e.get_fuel_inventory=e.get_fuel_inventory or function() return nil end
            e.get_output_inventory=e.get_output_inventory or function() return nil end
            e.is_crafting=e.is_crafting or function() return false end
        end
        player.get_max_inventory_index=function() return 1 end
        player.get_inventory=function() return {valid=true,get_contents=function() return {} end} end
        game.surfaces={surface}
    ''')
    lua.execute(fixed_query())
    raw = plain(lua.globals().projected)
    assert raw['query_status'] == 'observed', raw['reason']
    alternative = raw['material_census']['alternative_stock']
    assert alternative['tree_count'] == 0
    assert alternative['scope'] == 'actor_and_copper_consumer_radius_64'
    assert alternative['unobserved_current_surface_outside_scope'] is True
    evidence = checked(raw, bundle)
    assert evidence.alternatives.current_surface_coverage_complete is False
