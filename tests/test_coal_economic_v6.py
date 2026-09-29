"""Private v6 material-census source prototype; no engine qualification."""
from copy import deepcopy

import pytest

from jev_factorio.coal_economic_observation import NativeEconomicsUnavailable, UNIT_QUALIFICATION
from jev_factorio.coal_economic_v6 import (
    CYCLE_SOURCE_SHA256, SCHEMA, decode_v6, fixed_query, query_sha256,
)
from jev_factorio.backends.native_attachment import (
    connector_observer_bridge_sha256, manual_journal_sha256)
from test_coal_native_evidence import example, lua_runtime, plain


def _installed_v6_mock(lua):
    lua.globals().v1_hash = manual_journal_sha256()
    lua.globals().v2_hash = CYCLE_SOURCE_SHA256
    lua.globals().bridge_hash = connector_observer_bridge_sha256()
    lua.execute('''
        local rt=jev_fle_runtime
        local journal={protocol=1,pending=nil,rows={},order={},
            session_id=rt.jev_session_id,actor_index=1,
            actor_unit=actor.unit_number,surface_index=surface.index,
            force_index=force.index,tick_handler=function() end}
        local cycle={protocol=2,active=nil,rows={},order={},
            session_id=rt.jev_session_id,actor_index=1,
            actor_unit=actor.unit_number,surface_index=surface.index,
            force_index=force.index,tick_handler=function() end,
            combined_tick_handler=function() end}
        rt.coal_manual_journal_v1=journal
        rt.coal_manual_cycle_v2=cycle
        rt.connector_observer_bridge_v1={protocol=1,snapshot_qualified=true,
            snapshot_tick=9000,snapshot_ownership={protocol=1,
                session_id=rt.jev_session_id,tick=9000,routes={}}}
        rt.native_installation={
            profile='e759-observation-v2-water-origin-v4-manual-cycle-v6-connector-observer-v1',
            assets={coal_manual_journal_v1=v1_hash,coal_manual_cycle_v2=v2_hash,
                connector_observer_bridge_v1=bridge_hash},
            callbacks={journal_tick=journal.tick_handler,
                cycle_tick=cycle.combined_tick_handler}}
        campaign.receipt_order={};campaign.receipts={}
    ''')


def projected(extra_lua=''):
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    _installed_v6_mock(lua)
    lua.execute('''
        local old_search=surface.find_entities_filtered
        surface.find_entities_filtered=function(options)
            if options.area then return old_search(options) end
            if options.force==force then return all_entities end
            if type(options.type)=='table' then return all_entities end
            if options.type=='item-entity' or options.type=='tree' then return {} end
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
    if extra_lua:
        lua.execute(extra_lua)
    lua.execute(fixed_query())
    result = plain(lua.globals().projected)
    return result, bundle


def checked(raw, bundle):
    return decode_v6(raw, expected_epoch=deepcopy(raw['epoch']),
                     expected_bundle=bundle,
                     unit_qualification=deepcopy(UNIT_QUALIFICATION),
                     expected_journal_asset_sha256=manual_journal_sha256())


def test_composed_query_uses_entire_graph_and_bounded_material_census():
    raw, bundle = projected()
    assert raw['query_status'] == 'observed', raw['reason']
    assert raw['schema'] == SCHEMA
    assert raw['material_census']['owned_surface_coverage_complete'] is True
    assert len(query_sha256()) == 64
    result = checked(raw, bundle)
    assert result.census.owned_surface_coverage_complete is True
    assert len(result.census.entities) == len(raw['registry'])
    assert result.native_payback_proven is False
    assert result.mutation_authorized is False


CYCLE_ROWS = '''
    local rt=jev_fle_runtime
    local journal=rt.coal_manual_journal_v1
    local cycle=rt.coal_manual_cycle_v2
    local g={receipt='gather',status='complete',pending=false,overflow=false,
        fault=false,session_id=rt.jev_session_id,actor_index=1,
        actor_unit=actor.unit_number,surface_index=surface.index,
        force_index=force.index,resource='coal',started_tick=100,
        finished_tick=105,coal_before=0,coal_after=3,
        walking_ticks=0,mining_ticks=4}
    journal.order={'gather'};journal.rows={gather=g}
    campaign.receipt_order={'first','second'}
    campaign.receipts={
        first={role='furnace',unit_number=campaign.entities.furnace.unit_number,
            item='coal',quantity=1,extracting=false,tick=110},
        second={role='utility:boiler',
            unit_number=campaign.entities['utility:boiler'].unit_number,
            item='coal',quantity=2,extracting=false,tick=115}}
    local d1={receipt='first',role='furnace',
        unit=campaign.entities.furnace.unit_number,coal=1,tick=110,
        started_tick=108,finished_tick=111,walking_ticks=1}
    local d2={receipt='second',role='utility:boiler',
        unit=campaign.entities['utility:boiler'].unit_number,coal=2,tick=115,
        started_tick=112,finished_tick=116,walking_ticks=1}
    cycle.order={'cycle'}
    cycle.rows={cycle={receipt='cycle',status='complete',fault=false,
        pending_delivery=nil,session_id=rt.jev_session_id,actor_index=1,
        actor_unit=actor.unit_number,surface_index=surface.index,
        force_index=force.index,gather_receipt='gather',
        started_tick=99,finished_tick=120,expected_coal=0,
        receipt_count=0,receipt_end=2,
        gather={started_tick=100,finished_tick=105,coal_before=0,
            coal_after=3,walking_ticks=0,mining_ticks=4},
        deliveries={d1,d2}}}
'''


def test_native_projected_complete_receipt_rows_remain_diagnostic():
    raw, bundle = projected(CYCLE_ROWS)
    assert raw['query_status'] == 'observed', raw['reason']
    result = checked(raw, bundle)
    assert len(result.cycles) == 1
    assert result.cycles[0].gathered_coal == 3
    assert result.cycles[0].delivered_coal == 3
    assert result.cycles[0].gather_mining_ticks == 4
    assert [row.role for row in result.cycles[0].deliveries] == ['furnace', 'utility:boiler']
    assert result.native_payback_proven is False
    assert result.mutation_authorized is False
    raw['cycle_v2']['rows'][0]['deliveries'][0]['unit'] = 999
    with pytest.raises(NativeEconomicsUnavailable, match='cycle_v2_delivery_mismatch'):
        checked(raw, bundle)


@pytest.mark.parametrize('extra,reason', [
    ("campaign.receipts.first.unit_number=999", 'material_census_cycle_delivery'),
    ("jev_fle_runtime.coal_manual_cycle_v2.active={receipt='unknown'}",
     'material_census_cycle_unqualified'),
    ("jev_fle_runtime.coal_manual_cycle_v2.rows.cycle.expected_coal=1",
     'material_census_cycle_conservation'),
])
def test_native_cycle_rechecks_receipt_pending_and_conservation(extra, reason):
    raw, _ = projected(CYCLE_ROWS + '\n' + extra)
    assert raw['query_status'] == 'unsupported'
    assert raw['reason'] == reason


@pytest.mark.parametrize('change', [
    lambda r: r['material_census']['entities'].pop(),
    lambda r: r['material_census']['entities'][0].update(unit=999),
    lambda r: r['material_census']['entities'][0].update(inventories=[
        {'index': 1, 'items': [{'name': 'coal', 'count': -1}]}]),
    lambda r: r['material_census'].update(trees=1),
    lambda r: r['material_census'].update(crafting_queue=1),
    lambda r: r['material_census'].update(owned_surface_coverage_complete=False),
])
def test_missing_foreign_or_unclosed_material_rejected(change):
    raw, bundle = projected()
    change(raw)
    with pytest.raises(NativeEconomicsUnavailable):
        checked(raw, bundle)


def test_foreign_player_force_entity_rejects_native_query():
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    _installed_v6_mock(lua)
    lua.execute('''
        local old_search=surface.find_entities_filtered
        surface.find_entities_filtered=function(options)
            if options.area then return old_search(options) end
            if options.force==force then
                local found={};for _,e in ipairs(all_entities) do found[#found+1]=e end
                found[#found+1]={valid=true,unit_number=99999,name='wooden-chest',
                    quality={name='normal'},surface=surface,force=force}
                return found
            end
            if type(options.type)=='table' then return all_entities end
            return {}
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
    result = plain(lua.globals().projected)
    assert result['query_status'] == 'unsupported'
    assert result['reason'] == 'material_census_foreign_entity'


@pytest.mark.parametrize('extra,reason', [
    ("game.surfaces={surface,{index=2}}", 'material_census_other_surface'),
    ("local previous=surface.find_entities_filtered; "
     "surface.find_entities_filtered=function(options) "
     "if type(options.type)=='table' then return {{valid=true,name='wooden-chest',unit_number=98765}} end "
     "return previous(options) end", 'material_census_alternate_stock'),
    ("local previous=surface.find_entities_filtered; "
     "surface.find_entities_filtered=function(options) "
     "if options.type=='tree' then return {{valid=true}} end "
     "return previous(options) end", 'material_census_tree_fuel'),
    ("by_unit[1005].type='underground-belt'", 'material_census_unsupported_carrier'),
])
def test_native_query_rejects_unclosed_world_source(extra, reason):
    raw, _ = projected(extra)
    assert raw['query_status'] == 'unsupported'
    assert raw['reason'] == reason
