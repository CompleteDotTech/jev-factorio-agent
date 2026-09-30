"""Executable mock checks for v7 current-goal admission and its write boundary.

These Lua fixtures verify protocol behavior only; they are not native-game
qualification or a throughput claim.
"""
from copy import deepcopy
import hashlib
import json
from importlib.resources import files

import pytest

from jev_factorio.backends.native_attachment import (
    MANUAL_CYCLE_PROFILE, manual_journal_sha256,
)
from jev_factorio.backends.fle import SessionRcon
from jev_factorio.coal_economic_observation import UNIT_QUALIFICATION
from jev_factorio.coal_economic_v7 import (
    NATIVE_ADMISSION_SOURCE_SHA256, V6_OBSERVER_PROFILE,
    decode_admission_journal_readback, decode_v7, fixed_admission_journal_readback_query,
    fixed_first_spend_query, fixed_query, query_sha256,
)
from jev_factorio.memory import CampaignMemory
from jev_factorio.planning.coal_admission import evaluate as evaluate_admission
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning import coal_funding
from jev_factorio.state import GameSnapshot
from coal_supply_fixtures import fixture as coal_supply_fixture
from test_coal_economic_v6 import _installed_v6_mock
from test_coal_native_evidence import example, lua_runtime, plain
from test_factory import catalog as base_catalog


def _remap_copper_target(raw, bundle):
    old, new = 'furnace', 'recipe:copper-plate'
    bundle[new] = bundle.pop(old)
    bundle[new]['target']['role'] = new
    for collection in ('registry', 'fuel_targets', 'sources'):
        for row in raw[collection]:
            key = 'role' if collection != 'sources' else 'target'
            if row[key] == old:
                row[key] = new
    for row in raw['material_scope']['owned_stock']:
        if row['role'] == old:
            row['role'] = new
    return raw, bundle


def _qualified_lua(*, target_ore=10, unit_count=10, ambiguous=False,
                   existing_coal=0, legacy_v1_history=False):
    raw, bundle = example()
    raw, bundle = _remap_copper_target(raw, bundle)
    for row in raw['fuel_targets']:
        row['coal'] = existing_coal
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
            journal.order={'gather-legacy'}
            journal.rows={['gather-legacy']={receipt='gather-legacy',
                status='complete',pending=false,overflow=false,fault=false,
                session_id=rt.jev_session_id,actor_index=1,
                actor_unit=actor.unit_number,surface_index=surface.index,
                force_index=force.index,resource='coal',started_tick=9000,
                finished_tick=9100,coal_before=0,coal_after=5,
                walking_ticks=20,mining_ticks=30}}
            campaign.receipt_order={'deliver-legacy-1','deliver-legacy-2'}
            campaign.receipts={
                ['deliver-legacy-1']={role='recipe:copper-plate',item='coal',quantity=2,
                    unit_number=campaign.entities['recipe:copper-plate'].unit_number,
                    extracting=false,tick=9200},
                ['deliver-legacy-2']={role='utility:boiler',item='coal',quantity=3,
                    unit_number=campaign.entities['utility:boiler'].unit_number,
                    extracting=false,tick=9300}}
        '''.replace('MANUAL_PROFILE', repr(MANUAL_CYCLE_PROFILE)))
    lua.execute('''
        defines.inventory={furnace_source=1,lab_input=2}
        actor.position={x=100.5,y=200.5}
        actor.prototype={mining_speed=.5}
        force.manual_mining_speed_modifier=0
        player.character_mining_speed_modifier=0
        game.surfaces={surface}
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
        local function inventory(rows)
            return {valid=true,get_contents=function() return rows end,
                get_item_count=function(name)
                    for _,row in ipairs(rows) do if row.name==name then return row.count end end
                    return 0
                end}
        end
        local main=inventory({
            {name='electric-mining-drill',count=2},
            {name='wooden-chest',count=16},
            {name='inserter',count=128},
            {name='transport-belt',count=256},
            {name='iron-plate',count=IRON_STOCK},
            {name='copper-ore',count=9}})
        player.get_main_inventory=function() return main end
        player.get_max_inventory_index=function() return 1 end
        player.get_inventory=function(i) return i==1 and main or nil end
        local lab=campaign.entities['utility:lab']
        local lab_input=inventory({})
        lab.get_max_inventory_index=function() return 2 end
        lab.get_inventory=function(i) return i==2 and lab_input or nil end
        local copper=campaign.entities['recipe:copper-plate']
        local copper_input=inventory({{name='copper-ore',count=TARGET_ORE}})
        local copper_fuel=copper.get_fuel_inventory()
        local copper_output=inventory({})
        copper.get_max_inventory_index=function() return 3 end
        copper.get_inventory=function(i)
            if i==1 then return copper_input end
            if i==2 then return copper_fuel end
            if i==3 then return copper_output end
        end
        copper.get_output_inventory=function() return copper_output end
        copper.get_recipe=function() return force.recipes['copper-plate'] end
        copper.is_crafting=function() return false end
        copper.crafting_speed=1
        copper.burner={fuel_categories={chemical=true},remaining_burning_fuel=0,
            heat=0,currently_burning=nil}
        local boiler=campaign.entities['utility:boiler']
        local boiler_fuel=boiler.get_fuel_inventory()
        boiler.get_max_inventory_index=function() return 1 end
        boiler.get_inventory=function(i) return i==1 and boiler_fuel or nil end
        boiler.burner={fuel_categories={chemical=true},remaining_burning_fuel=0,
            heat=0,currently_burning=nil}
        local iron_gear={enabled=true,hidden=false,energy=.5,
            products={{type='item',name='iron-gear-wheel',amount=1}},
            ingredients={{type='item',name='iron-plate',amount=2}}}
        force.recipes={
            ['automation-science-pack']={name='automation-science-pack',enabled=true,hidden=false,energy=5,
                products={{type='item',name='automation-science-pack',amount=1}},
                ingredients={{type='item',name='copper-plate',amount=1},
                    {type='item',name='iron-gear-wheel',amount=1}}},
            ['iron-gear-wheel']=iron_gear,
            ['copper-plate']={name='copper-plate',enabled=true,hidden=false,energy=3.2,
                products={{type='item',name='copper-plate',amount=1}},
                ingredients={{type='item',name='copper-ore',amount=1}}}}
        iron_gear.name='iron-gear-wheel'
        if AMBIGUOUS then
            force.recipes['alternate-copper-plate']={name='alternate-copper-plate',enabled=true,hidden=false,energy=4,
                products={{type='item',name='copper-plate',amount=1}},
                ingredients={{type='item',name='copper-ore',amount=1}}}
        end
        force.current_research={name='automation',research_unit_count=UNIT_COUNT,
            research_unit_energy=30,
            research_unit_ingredients={{name='automation-science-pack',amount=1}},
            prototype={ignore_tech_cost_multiplier=false},researched=false}
        force.research_progress=0
        force.technologies={
            ['rocket-silo']={name='rocket-silo',enabled=true,researched=false,
                prerequisites={automation=true}},
            automation={name='automation',enabled=true,researched=false,prerequisites={}}}
        game.difficulty_settings={technology_price_multiplier=1}
        prototypes.item.coal={fuel_value=4000000,fuel_category='chemical'}
        q.admission_evidence=true
        coal_request={target='recipe:copper-plate',layout=EXPECTED_LAYOUT,
            part='chest',receipt=string.rep('a',64)}
    '''.replace('TARGET_ORE', str(target_ore))
       .replace('UNIT_COUNT', str(unit_count))
       .replace('IRON_STOCK', str(max(64, unit_count * 2 + 64)))
       .replace('AMBIGUOUS', 'true' if ambiguous else 'false')
       .replace('EXPECTED_LAYOUT', repr(bundle['recipe:copper-plate']['layout'])))
    lua.execute('''
        helpers.json_to_table=function(_) return coal_request end
        helpers.table_to_json=function(value)
            response_table=value
            projected=value
            return '{}'
        end
        rcon.print=function(value) response_text=value end
        build_calls=0
        campaign.build_coal_source=function(request)
            build_calls=build_calls+1
            local role='coal:'..request.target..':chest'
            local entity={valid=true,unit_number=9001,name='wooden-chest',type='container',
                surface=surface,force=force,quality={name='normal'},position={x=101,y=201},
                bounding_box={left_top={x=100.5,y=200.5},right_bottom={x=101.5,y=201.5}},
                get_max_inventory_index=function() return 0 end,
                get_inventory=function() return nil end,
                get_fuel_inventory=function() return nil end,
                get_output_inventory=function() return nil end}
            campaign.entities[role]=entity
            all_entities[#all_entities+1]=entity
            q.rows[request.target].parts.chest={receipt=request.receipt,paid=1,
                role=role,unit_number=entity.unit_number,entity=entity}
            return {placed=true}
        end
        local q=jev_fle_runtime.coal_supply
        q.prepare=function(_) return {} end
        q.build=campaign.build_coal_source
        campaign.prepare_coal_source=q.prepare
        jev_fle_runtime.solid_routes={implementation_revision=4,coal=q,coal_api={}}
    ''')
    return lua, bundle


def _run_first_spend(lua, bundle, *, request_prepared=True):
    target = 'recipe:copper-plate'
    if request_prepared:
        lua.execute('''
            q.committed=true
            q.rows['recipe:copper-plate'].pending={phase='prepared',part='chest',
                receipt=coal_request.receipt}
        ''')
    source = fixed_first_spend_query({
        'target': target, 'layout': bundle[target]['layout'], 'part': 'chest',
        'receipt': 'a' * 64,
    })
    lua.execute(source)
    return plain(lua.globals().response_table), source


def test_fixed_queries_use_runtime_namespace_without_relying_on_scoped_storage_alias():
    read_query = fixed_query()
    action_query = fixed_first_spend_query({
        'target': 'recipe:copper-plate', 'layout': 'pinned-layout',
        'part': 'chest', 'receipt': 'a' * 64,
    })
    journal_query = fixed_admission_journal_readback_query()
    for source in (read_query, action_query, journal_query):
        assert 'storage.coal_native_admission_journal_v1' not in source
        assert 'rt.coal_native_admission_journal_v1' in source
        assert 'local storage = jev_fle_runtime' not in source
        assert 'local rt=jev_fle_runtime' in source
    # Production /sc commands receive a storage alias for legacy modules, but
    # the v7 guard, census, and action remain correct from rt alone.
    scoped = SessionRcon.scoped('/sc ' + action_query)
    assert scoped.startswith('/sc local storage = jev_fle_runtime; local coal_native_request=')
    assert 'local rt=jev_fle_runtime' in scoped


def test_native_first_spend_rechecks_current_goal_and_builds_once_in_same_rpc():
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500)
    envelope, source = _run_first_spend(lua, bundle)
    assert envelope['query_status'] == 'observed', envelope
    assert envelope['admission']['qualified'] is True, envelope['admission']
    assert envelope['admission']['mutation_authorized'] is False
    assert envelope['admission']['time_to_completion_claimed'] is False
    assert envelope['admission']['direct_target'] == 'recipe:copper-plate'
    assert envelope['admission']['rocket_goal_path'] == ['rocket-silo', 'automation']
    assert envelope['admission']['copper_plate_ore_input_required'] == 1500
    assert envelope['admission']['copper_plate_ore_input_available'] == 1500
    assert envelope['admission']['manual_copper_mining_ticks_estimate_no_walk'] > (
        envelope['admission']['remaining_project_setup_ticks_estimate'])
    assert envelope['action'] == {'status': 'placed', 'result': {'placed': True}}
    assert envelope['attempt']['phase'] == 'paid'
    assert envelope['attempt']['source_asset_sha256'] == NATIVE_ADMISSION_SOURCE_SHA256
    assert envelope['attempt']['paid']['role'] == 'coal:recipe:copper-plate:chest'
    assert lua.globals().jev_fle_runtime.coal_native_admission_journal_v1.rows[
        'a' * 64].phase == 'paid'
    journal = lua.globals().jev_fle_runtime.coal_native_admission_journal_v1
    assert journal.guard_asset_sha256 == NATIVE_ADMISSION_SOURCE_SHA256
    assert journal.native_profile == V6_OBSERVER_PROFILE
    assert journal.legacy_manual_history.status == 'observed'
    assert lua.globals().jev_fle_runtime.native_installation.profile == V6_OBSERVER_PROFILE
    guard_bytes = files('jev_factorio').joinpath(
        'lua/coal_native_admission_v1.lua').read_bytes()
    assert hashlib.sha256(guard_bytes).hexdigest() == NATIVE_ADMISSION_SOURCE_SHA256
    assert source.count('local function coal_native_admission_v1') == 1
    assert source.count('rt.campaign.build_coal_source(coal_native_request)') == 1
    lua.execute(fixed_admission_journal_readback_query())
    readback = plain(lua.globals().projected)
    decoded = decode_admission_journal_readback(
        readback, expected_epoch=deepcopy(readback['epoch']),
        expected_native_profile=V6_OBSERVER_PROFILE)
    assert decoded['rows'][0]['phase'] == 'paid'
    assert decoded['rows'][0]['receipt'] == 'a' * 64
    assert decoded['rows'][0]['current_payment']['role'] == 'coal:recipe:copper-plate:chest'
    assert lua.globals().build_calls == 1
    # The first-spend proof and action are one fixed command, with only the
    # prepared chest receipt substituted into the immutable query template.
    assert source.count('rt.campaign.build_coal_source(coal_native_request)') == 1
    assert lua.globals().game.tick == 10000


def test_local_alternative_census_binds_to_actual_current_copper_consumer():
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500)
    lua.execute(fixed_query())
    raw = plain(lua.globals().projected)
    assert raw['query_status'] == 'observed', raw['reason']
    mutated = deepcopy(raw)
    mutated['material_census']['alternative_stock']['copper_consumer_position']['x'] += 1
    with pytest.raises(ValueError, match='material_census_consumer_position_mismatch'):
        decode_v7(mutated, expected_epoch=deepcopy(mutated['epoch']),
                  expected_bundle=bundle,
                  unit_qualification=deepcopy(UNIT_QUALIFICATION),
                  expected_journal_asset_sha256=manual_journal_sha256())


@pytest.mark.parametrize('source_kind', ['ground', 'tree'])
def test_uncollected_local_fuel_is_costed_not_counted_as_owned_energy(source_kind):
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500)
    if source_kind == 'ground':
        lua.execute('''
            prototypes.item.wood={fuel_value=2000000,fuel_category='chemical'}
            ground_entities={{valid=true,type='item-entity',surface=surface,
                unit_number=8801,position={x=101,y=201},
                    stack={valid_for_read=true,name='wood',count=50,
                    quality={name='normal'}}}}
            tree_entities={}
        ''')
    else:
        lua.execute('''
            prototypes.item.wood={fuel_value=2000000,fuel_category='chemical'}
            ground_entities={}
            tree_entities={{valid=true,type='tree',surface=surface,unit_number=8802,
                name='local-tree',position={x=101,y=201},
                prototype={mineable_properties={minable=true,required_fluid=nil,
                    mining_time=10,products={{type='item',name='wood',amount=50,
                        probability=1}}}}}}
        ''')
    envelope, _ = _run_first_spend(lua, bundle)
    assert envelope['query_status'] == 'observed', envelope
    proof = envelope['admission']
    assert proof['qualified'] is True, proof
    assert proof['current_copper_alternative_fuel_joules_upper'] == 0
    assert proof['manual_local_collection_source_count_estimate'] == 1
    assert proof['manual_local_collection_fuel_joules_estimate'] > 0
    assert proof['manual_fuel_service_ticks_estimate'] < (
        proof['manual_copper_mining_ticks_estimate_no_walk'])
    assert proof['manual_fuel_service_ticks_estimate'] > (
        proof['remaining_project_setup_ticks_estimate'])
    assert proof['local_scope_only'] is True
    assert proof['observed_generated_entities_only'] is True
    assert proof['unobserved_chunks_inside_scope_unknown'] is True
    assert proof['unobserved_current_surface_outside_scope'] is True


def test_v5_first_spend_keeps_legacy_history_and_records_one_use_guard_receipt():
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500,
                                 legacy_v1_history=True)
    envelope, _ = _run_first_spend(lua, bundle)
    assert envelope['action'] == {'status': 'placed', 'result': {'placed': True}}
    assert envelope['attempt']['phase'] == 'paid'
    assert envelope['attempt']['source_asset_sha256'] == NATIVE_ADMISSION_SOURCE_SHA256
    assert lua.globals().jev_fle_runtime.native_installation.profile == MANUAL_CYCLE_PROFILE
    assert lua.globals().jev_fle_runtime.coal_manual_cycle_v2 is None
    assert list(lua.globals().jev_fle_runtime.coal_manual_journal_v1.order.values()) == [
        'gather-legacy']
    assert lua.globals().jev_fle_runtime.campaign.receipts['deliver-legacy-1'].quantity == 2
    assert lua.globals().jev_fle_runtime.campaign.receipts['deliver-legacy-2'].quantity == 3
    assert lua.globals().jev_fle_runtime.coal_native_admission_journal_v1.rows[
        'a' * 64].phase == 'paid'
    lua.execute(fixed_admission_journal_readback_query())
    readback = plain(lua.globals().projected)
    decoded = decode_admission_journal_readback(
        readback, expected_epoch=deepcopy(readback['epoch']),
        expected_native_profile=MANUAL_CYCLE_PROFILE)
    assert decoded['legacy_manual_history']['gathers'][0]['receipt'] == 'gather-legacy'
    assert [row['receipt'] for row in decoded['legacy_manual_history']['deliveries']] == [
        'deliver-legacy-1', 'deliver-legacy-2']
    assert decoded['rows'][0]['phase'] == 'paid'
    assert lua.globals().jev_fle_runtime.native_installation.profile == MANUAL_CYCLE_PROFILE
    assert lua.globals().jev_fle_runtime.coal_manual_cycle_v2 is None


def test_larger_direct_research_bill_passes_native_first_spend_once():
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500)
    envelope, _ = _run_first_spend(lua, bundle)
    assert envelope['admission']['qualified'] is True, envelope['admission']
    assert envelope['admission']['copper_plate_recipe_batches'] == 1500
    assert envelope['admission']['manual_copper_coal_units_estimate'] > 1
    assert envelope['admission']['manual_copper_mining_ticks_estimate_no_walk'] > 0
    assert envelope['action']['status'] == 'placed'
    assert lua.globals().build_calls == 1


def test_first_paid_component_still_requires_the_complete_carried_kit():
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500)
    lua.execute('''
        local inventory=player.get_main_inventory()
        local old_count=inventory.get_item_count
        inventory.get_item_count=function(name)
            if name=='transport-belt' then return 0 end
            return old_count(name)
        end
    ''')
    envelope, _ = _run_first_spend(lua, bundle)
    assert envelope['admission']['qualified'] is False
    assert envelope['admission']['reason'] == 'whole_coal_kit_not_carried'
    assert envelope['action']['status'] == 'not_dispatched'
    assert lua.globals().build_calls == 0
    assert lua.globals().jev_fle_runtime.coal_native_admission_journal_v1 is None


def test_ambiguous_first_payment_is_readable_but_never_replayed():
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500)
    lua.execute('''
        q.committed=true
        q.rows['recipe:copper-plate'].pending={phase='prepared',part='chest',
            receipt=coal_request.receipt}
    ''')
    lua.execute('''
        campaign.build_coal_source=function(_)
            build_calls=build_calls+1
            error('simulated lost native acknowledgement')
        end
        jev_fle_runtime.coal_supply.build=campaign.build_coal_source
    ''')
    source = fixed_first_spend_query({
        'target': 'recipe:copper-plate', 'layout': bundle['recipe:copper-plate']['layout'],
        'part': 'chest', 'receipt': 'a' * 64,
    })
    with pytest.raises(Exception):
        lua.execute(source)
    assert lua.globals().build_calls == 1

    # The durable native journal must expose the ambiguous attempt without
    # changing it. A retry of the same prepared receipt then fails closed
    # before the builder, and another readback still reports the original
    # unknown state rather than a fresh dispatch.
    lua.execute(fixed_admission_journal_readback_query())
    before_retry = plain(lua.globals().projected)
    decoded_before = decode_admission_journal_readback(
        before_retry, expected_epoch=deepcopy(before_retry['epoch']),
        expected_native_profile=V6_OBSERVER_PROFILE)
    assert decoded_before['rows'][0]['phase'] == 'unknown'
    assert decoded_before['rows'][0]['paid'] is False
    assert decoded_before['rows'][0]['current_payment'] is False

    lua.execute(source)
    retry = plain(lua.globals().response_table)
    assert retry['action']['status'] == 'not_dispatched'
    assert retry['attempt']['phase'] == 'not_dispatched'
    assert lua.globals().build_calls == 1

    lua.execute(fixed_admission_journal_readback_query())
    after_retry = plain(lua.globals().projected)
    decoded_after = decode_admission_journal_readback(
        after_retry, expected_epoch=deepcopy(after_retry['epoch']),
        expected_native_profile=V6_OBSERVER_PROFILE)
    assert decoded_after['rows'][0]['phase'] == 'unknown'
    assert decoded_after['rows'][0]['paid'] is False
    assert decoded_after['rows'][0]['current_payment'] is False


def test_changed_installed_coal_builder_cannot_pass_first_payment_guard():
    lua, bundle = _qualified_lua()
    lua.execute('''
        campaign.build_coal_source=function(_)
            build_calls=build_calls+1
            return {placed=true}
        end
        q.committed=true
        q.rows['recipe:copper-plate'].pending={phase='prepared',part='chest',
            receipt=coal_request.receipt}
    ''')
    source = fixed_first_spend_query({
        'target': 'recipe:copper-plate', 'layout': bundle['recipe:copper-plate']['layout'],
        'part': 'chest', 'receipt': 'a' * 64,
    })
    lua.execute(source)
    envelope = plain(lua.globals().response_table)
    assert envelope['admission']['qualified'] is False
    assert envelope['admission']['reason'] == 'native_coal_builder_unqualified'
    assert envelope['action']['status'] == 'not_dispatched'
    assert lua.globals().build_calls == 0
    # Builder identity is checked before the write-ahead attempt row. This is
    # a proved no-dispatch rejection, so it must not fabricate an attempt.
    assert lua.globals().jev_fle_runtime.coal_native_admission_journal_v1 is None
    lua.execute(source)
    assert lua.globals().response_table.action.status == 'not_dispatched'
    assert lua.globals().build_calls == 0


def _goal_projection():
    # The old 10-pack fixture cannot cover even a small construction forecast.
    # This larger finite bill makes a positive, explicitly estimated case.
    lua, bundle = _qualified_lua(target_ore=1500, unit_count=1500)
    lua.execute(fixed_query())
    raw = plain(lua.globals().projected)
    native = decode_v7(raw, expected_epoch=deepcopy(raw['epoch']),
        expected_bundle=bundle, unit_qualification=deepcopy(UNIT_QUALIFICATION),
        expected_journal_asset_sha256=manual_journal_sha256())
    response = json.dumps(raw, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True, allow_nan=False)
    projection = {
        'native': native,
        'raw_response': response,
        'query_sha256': query_sha256(),
        'response_sha256': hashlib.sha256(response.encode('utf-8')).hexdigest(),
        'coal_admission': native.native_admission,
    }
    snapshot = GameSnapshot(
        tick=raw['epoch']['tick'], session_id=raw['epoch']['session_id'],
        world_kind='fle', game_version='2.0.77', researched=[])
    snapshot.factory = {
        'research': 'automation',
        'acceptance_runtime': {
            'session_id': snapshot.session_id, 'tick': snapshot.tick,
            'actor_unit': raw['epoch']['actor_unit'], 'player_index': 1,
            'surface_index': raw['epoch']['surface_index'],
            'force_index': raw['epoch']['force_index']},
        'coal_supply': {
            'actor_index': raw['epoch']['actor_index'],
            'surface_index': raw['epoch']['surface_index'],
            'force_index': raw['epoch']['force_index']}}
    memory = CampaignMemory(snapshot.session_id, 'rocket_launch',
        active_goal='rocket_launch', last_tick=snapshot.tick)
    memory.coal_economic_admission = True
    memory.coal_kit_policy = True
    data = base_catalog()
    study = {'enabled': True, 'effects': [], 'prerequisites': [], 'trigger': False,
             'count': 1500, 'energy_ticks': 1800,
             'ingredients': [{'name': 'automation-science-pack', 'amount': 1}]}
    data.technologies['automation'] = deepcopy(study)
    data.technologies['rocket-silo'] = {**deepcopy(study),
        'prerequisites': ['automation']}
    catalog = Catalog('2.0.77', data.recipes, data.technologies, data.machines,
                      data.hand_categories, data.stack_sizes)
    setup_snapshot = coal_supply_fixture()
    setup_snapshot.player_position = (100.5, 200.5)
    _, acquisition = coal_funding.candidate(setup_snapshot, base_catalog())
    projection['project_setup_cost_estimate'] = (
        coal_funding.project_setup_cost_estimate(setup_snapshot, acquisition))
    return snapshot, memory, catalog, projection


def test_current_goal_candidate_requires_complete_fresh_native_proof():
    snapshot, memory, catalog, projection = _goal_projection()
    result = evaluate_admission(snapshot, memory, catalog, projection)
    assert result['eligible'] is True, result
    assert result['technology'] == 'automation'
    assert result['rocket_goal_path'] == ['rocket-silo', 'automation']
    assert result['copper_plate_recipe_batches'] == 1500
    assert result['copper_plate_ore_input_required'] == 1500
    assert result['copper_plate_ore_input_available'] == 1500
    assert result['manual_copper_mining_ticks_estimate_no_walk'] > (
        result['whole_project_setup_ticks_estimate'])
    assert result['mutation_authorized'] is False
    assert result['native_payback_proven'] is False
    assert result['future_generated_chunks_unknown'] is True


@pytest.mark.parametrize('change', [
    lambda args: setattr(args[0], 'session_id', 'different-session'),
    lambda args: setattr(args[1], 'active_goal', 'bootstrap_mining'),
    lambda args: args[0].factory.update(research='different-research'),
    lambda args: args[3].update(response_sha256='0' * 64),
    lambda args: args[3]['coal_admission'].update(mutation_authorized=True),
])
def test_changed_goal_or_native_hash_never_makes_candidate_eligible(change):
    args = list(_goal_projection())
    change(args)
    result = evaluate_admission(*args)
    assert result['eligible'] is False
    assert result['mutation_authorized'] is False


def test_project_setup_forecast_must_be_currently_positive_and_well_formed():
    snapshot, memory, catalog, projection = _goal_projection()
    setup = projection['project_setup_cost_estimate']
    assert setup['placement_count'] == 10
    assert setup['placement_service_ticks_estimate'] == 10 * 300
    assert setup['placement_travel_ticks_estimate'] == 6540
    assert setup['total_setup_ticks_estimate'] < (
        projection['coal_admission']['manual_copper_mining_ticks_estimate_no_walk'])
    expensive = deepcopy(projection)
    expensive['project_setup_cost_estimate'].update({
        'acquisition_ticks_estimate': 10000,
        'total_setup_ticks_estimate': 19540,
    })
    assert evaluate_admission(snapshot, memory, catalog, expensive)['reason'] == (
        'manual_fuel_service_forecast_not_positive')
    malformed = deepcopy(projection)
    malformed['project_setup_cost_estimate']['measured'] = True
    assert evaluate_admission(snapshot, memory, catalog, malformed)['eligible'] is False
    malformed = deepcopy(projection)
    malformed['project_setup_cost_estimate']['placement_travel_ticks_estimate'] = 0
    assert evaluate_admission(snapshot, memory, catalog, malformed)['eligible'] is False


@pytest.mark.parametrize(('kwargs', 'reason'), [
    ({'target_ore': 1}, 'copper_target_input_does_not_cover_current_goal'),
    ({'unit_count': 1}, 'manual_fuel_service_forecast_not_positive'),
    ({'ambiguous': True}, 'research_recipe_ambiguous'),
    ({'existing_coal': 10}, 'copper_coal_net_margin_missing'),
])
def test_unqualified_native_current_goal_never_calls_first_payment(kwargs, reason):
    lua, bundle = _qualified_lua(**kwargs)
    envelope, _ = _run_first_spend(lua, bundle)
    assert envelope['admission']['qualified'] is False, envelope
    assert envelope['action']['status'] == 'not_dispatched'
    assert lua.globals().build_calls == 0
    assert envelope['admission']['reason'] == reason
