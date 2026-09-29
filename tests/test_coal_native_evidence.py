"""Synthetic native projection contracts, never engine/payment acceptance."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from importlib.resources import files
import math

import pytest

from jev_factorio import coal_supply
from jev_factorio.coal_economic_observation import (
    NativeEconomicsUnavailable, POLE_RADII, RATES, SCHEMA, UNIT_QUALIFICATION, decode, overlaps)
from coal_supply_fixtures import fixture


def example():
    observed = fixture()
    bundle = {}
    for original, target, name in (('alpha', 'furnace', 'stone-furnace'),
                                   ('beta', 'utility:boiler', 'boiler')):
        saved = coal_supply.commitment(coal_supply.sources(observed)[original])
        saved['target'].update(role=target, name=name)
        bundle[target] = saved
    raw = {'schema': SCHEMA, 'base_version': '2.0.77', 'mods': {'base': '2.0.77'},
        'query_status': 'observed', 'reason': 'bounded_native_projection',
        'epoch': {'session_id': 'explicit-synthetic-native-graph', 'tick': 10000,
                  'actor_index': 1, 'actor_unit': 999999, 'surface_index': 1, 'force_index': 1},
        'registry': [], 'connector_routes': [], 'prototypes': [], 'buffer_witnesses': [], 'poles': [], 'supply_surveys': [],
        'electric_members': [], 'fluid_members': [], 'fuel_targets': [], 'sources': [],
        'material_scope': {'status': 'observed', 'reason': 'partial_actor_and_fuel_targets',
                           'closure_complete': False, 'actor_unit': 999999,
                           'actor_items': [], 'crafting_queue': 0, 'owned_stock': []},
        'research_work': {'status': 'unavailable', 'reason': 'no_current_research',
                          'technology': '', 'progress': 0, 'unit_count': 0,
                          'cost_multiplier': 0, 'ignore_cost_multiplier': False,
                          'unit_energy': 0, 'ingredients': [], 'lab': {}, 'targets': []},
        'manual_cycle': {'status': 'unavailable', 'reason': 'journal_not_installed',
                         'journal_asset_sha256': '', 'gathers': [], 'deliveries': []},
        'coal_fuel_joules': 4_000_000,
        'fluid_prototypes': [{'name': 'steam', 'heat_capacity': 200, 'default_temperature': 15, 'max_temperature': 5000},
                             {'name': 'water', 'heat_capacity': 2000, 'default_temperature': 15, 'max_temperature': 100}],
        'pole_prototypes': [{'name': name, 'supply_radius': radius} for name, radius in sorted(POLE_RADII.items())]}
    for name, (source, usage, production, drain) in sorted(RATES.items()):
        row = {'name': name, 'source': source, 'max_usage': usage, 'max_production': production,
               'drain': drain, 'buffer_capacity': 0}
        if source == 'burner': row['burner_efficiency'] = 1
        if name == 'boiler': row.update(target_temperature=165, boiler_mode='output-to-separate-pipe')
        if name == 'steam-engine': row.update(generator_efficiency=1, fluid_usage_per_tick=.5, maximum_temperature=165)
        if name == 'electric-mining-drill': row['mining_speed'] = .5
        raw['prototypes'].append(row)
    def register(role, unit, name, position, bounds=None):
        x, y = position['x'], position['y']
        entry = {'role': role, 'unit': unit, 'name': name, 'quality': 'normal',
            'surface_index': 1, 'force_index': 1, 'position': deepcopy(position),
            'bounds': deepcopy(bounds) if bounds else {'left_top': {'x': x-.4, 'y': y-.4},
                                                      'right_bottom': {'x': x+.4, 'y': y+.4}}}
        raw['registry'].append(entry)
        return entry
    coverage = {}
    for target, saved in bundle.items():
        e = saved['target']; register(target, e['unit_number'], e['name'], e['position'], e['bounds'])
        raw['fuel_targets'].append({'role': target, 'unit': e['unit_number'], 'name': e['name'],
            'coal': 10, 'burning': '', 'remaining_burning_fuel': 0, 'heat': 0,
            'operation': {'active': True, 'status': 'working', 'control_behavior_present': False}})
        raw['material_scope']['owned_stock'].append({'role': target, 'unit': e['unit_number'],
            'fuel': [{'name': 'coal', 'count': 10}], 'output': []})
        a, b = saved['mining_area']['left_top'], saved['mining_area']['right_bottom']
        raw['sources'].append({**{k: deepcopy(saved[k]) for k in ('layout', 'mining_area', 'steps',
            'corridor', 'drill_bounds', 'chest_bounds')}, 'target': target, 'target_unit': e['unit_number'],
            'resources': [{'name': 'coal', 'position': {'x': (a['x']+b['x'])/2, 'y': (a['y']+b['y'])/2},
                           'amount': 5000, 'mining_time': 1}], 'neighbor_drills': []})
        coverage[target + ':drill'] = deepcopy(saved['drill_bounds'])
        for step in saved['corridor']:
            if step['name'] == 'inserter':
                x, y = step['position']['x'], step['position']['y']
                coverage[target + ':' + step['part']] = {'left_top': {'x': x-.15, 'y': y-.15},
                                                       'right_bottom': {'x': x+.15, 'y': y+.15}}
    origin = bundle['utility:boiler']['target']['position']
    for unit, role, name in ((1004, 'witness:drill', 'electric-mining-drill'),
                             (1005, 'witness:inserter', 'inserter'),
                             (1006, 'witness:inserter:second', 'inserter')):
        register(role, unit, name, {'x': origin['x']+100+unit-1004, 'y': origin['y']+100})
        if unit != 1006:
            raw['buffer_witnesses'].append({'name': name, 'unit': unit, 'capacity': 1000})
    raw['buffer_witnesses'].sort(key=lambda row: row['name'])
    for unit, role, name in ((1001, 'utility:engine', 'steam-engine'), (1002, 'utility:lab', 'lab')):
        e = register(role, unit, name, {'x': origin['x']+unit-1000, 'y': origin['y']})
        raw['electric_members'].append({'unit': unit, 'network_id': 7, 'buffer_capacity': 1000,
            'energy': 500, 'drain': 0, 'operation': {'active': True, 'status': 'working', 'control_behavior_present': False}})
        coverage['unit:' + str(unit)] = e['bounds']
    register('utility:water', 1003, 'offshore-pump', {'x': origin['x']-2, 'y': origin['y']})
    pole_bounds = {}
    for i, area in enumerate(coverage.values()):
        a, b = area['left_top'], area['right_bottom']; x, y = (a['x']+b['x'])/2, (a['y']+b['y'])/2
        unit = 2000+i; register('pole:' + str(unit), unit, 'substation', {'x': x, 'y': y})
        raw['poles'].append({'unit': unit, 'network_id': 7, 'supply_radius': 9, 'neighbors': []})
        pole_bounds[unit] = {'left_top': {'x': x-9, 'y': y-9}, 'right_bottom': {'x': x+9, 'y': y+9}}
    for left, right in zip(raw['poles'], raw['poles'][1:]):
        left['neighbors'].append(right['unit']); right['neighbors'].append(left['unit'])
    owned = {e['unit']: e for e in raw['registry']}
    for unit, area in pole_bounds.items():
        raw['supply_surveys'].append({'kind': 'supply', 'key': str(unit), 'bounds': area,
            'members': [e['unit'] for e in raw['electric_members'] if overlaps(area, owned[e['unit']]['bounds'])]})
    for key, area in coverage.items():
        raw['supply_surveys'].append({'kind': 'coverage', 'key': key, 'bounds': area,
            'poles': [unit for unit, pole in pole_bounds.items() if overlaps(area, pole)]})
    boiler_unit = bundle['utility:boiler']['target']['unit_number']
    def fluidbox(index, segment, kind, peer, peer_index):
        return {'index': index, 'segment_id': segment, 'capacity': 400, 'filter': kind,
            'fluid': kind, 'amount': 100, 'temperature': 15 if kind == 'water' else 165,
            'connections': [{'unit': peer, 'index': peer_index}]}
    raw['fluid_members'] = [
        {'unit': boiler_unit, 'boxes': [fluidbox(1, 10, 'water', 1003, 1), fluidbox(2, 11, 'steam', 1001, 1)]},
        {'unit': 1001, 'boxes': [fluidbox(1, 11, 'steam', boiler_unit, 2)]},
        {'unit': 1003, 'boxes': [fluidbox(1, 10, 'water', boiler_unit, 1)]}]
    for member in raw['fluid_members']:
        member['operation'] = {'active': True, 'status': 'working', 'control_behavior_present': False}
    raw['registry'].sort(key=lambda x: x['role']); raw['fluid_members'].sort(key=lambda x: x['unit'])
    raw['material_scope']['owned_stock'].sort(key=lambda x: x['role'])
    raw['supply_surveys'].sort(key=lambda x: (x['kind'], x['key']))
    return raw, bundle


def checked(raw, bundle, qualification=None):
    return decode(raw, expected_epoch=deepcopy(raw['epoch']), expected_bundle=bundle,
                  unit_qualification=deepcopy(UNIT_QUALIFICATION) if qualification is None else qualification)


def test_bounded_owned_graph_derives_cost_without_authorizing_gameplay():
    raw, bundle = example(); before = deepcopy(raw)
    result = checked(raw, bundle)
    assert result.power.buffer_capacity_joules_upper == 12_008_000  # Steam segment charged once.
    assert result.power.boiler_stored_fuel_joules == 40_000_000
    assert result.power.existing_loads[0].name == 'lab'
    assert result.power.existing_loads[0].max_joules_per_tick == 1000
    assert result.sources[0].remaining_ore == 5000
    assert result.mutation_authorized is False and result.native_payback_proven is False
    assert raw == before
    with pytest.raises(FrozenInstanceError): result.actor_unit = 2


def test_paid_connector_registry_requires_exact_checkpoint_identity():
    raw, bundle = example()
    role = 'connector:' + 'a' * 64 + ':1'
    position = {'x': 100.5, 'y': 200.5}
    raw['registry'].append({'role': role, 'unit': 9000, 'name': 'pipe',
        'quality': 'normal', 'surface_index': raw['epoch']['surface_index'],
        'force_index': raw['epoch']['force_index'], 'position': position,
        'bounds': {'left_top': {'x': 100.1, 'y': 200.1},
                   'right_bottom': {'x': 100.9, 'y': 200.9}}})
    raw['registry'].sort(key=lambda row: row['role'])
    expected = {role: {'unit': 9000, 'name': 'pipe', 'position': position}}
    roles = {row['role']: row['unit'] for row in raw['registry']}
    route = {'id': 'a' * 64, 'source': 'utility:boiler', 'target': 'utility:engine',
             'source_unit': roles['utility:boiler'], 'target_unit': roles['utility:engine'],
             'kind': 'pipe', 'fluid': 'steam', 'actor_unit': raw['epoch']['actor_unit'],
             'session_id': raw['epoch']['session_id'], 'surface_index': raw['epoch']['surface_index'],
             'force_index': raw['epoch']['force_index'], 'state': 'complete', 'owned': True,
             'paid': 1, 'cell_count': 1}
    raw['connector_routes'] = [route]
    def bound(value, binding):
        return decode(value, expected_epoch=deepcopy(value['epoch']),
                      expected_bundle=bundle, unit_qualification=UNIT_QUALIFICATION,
                      expected_connectors=binding, expected_routes={route['id']: route})
    assert bound(raw, expected).mutation_authorized is False
    for changed in ({}, {role: {**expected[role], 'unit': 9001}},
                    {role: {**expected[role], 'name': 'small-electric-pole'}},
                    {role: {**expected[role], 'position': {'x': 101.5, 'y': 200.5}}}):
        with pytest.raises(NativeEconomicsUnavailable):
            bound(raw, changed)
    raw['registry'][-1]['force_index'] += 1
    with pytest.raises(NativeEconomicsUnavailable):
        bound(raw, expected)


@pytest.mark.parametrize('case', ['unsupported', 'eligible', 'units', 'mod', 'prototype', 'bool_rate',
    'unknown_load', 'split_power', 'missing_pole', 'asymmetric_wire', 'missing_supply', 'missing_coverage',
    'foreign_coverage', 'missing_engine_fluid', 'missing_pipe_peer', 'asymmetric_pipe', 'wrong_segment',
    'segment_capacity', 'hot_water', 'mixed_fluid', 'double_boiler', 'source_unit', 'source_geometry',
    'resource_alias', 'foreign_drill', 'wrong_fuel', 'aliased_owner', 'foreign_surface', 'unpriced_buffer',
    'missing_witness', 'foreign_witness', 'unpriced_planned_buffer'])
def test_unsupported_or_inconsistent_raw_facts_never_qualify(case):
    raw, bundle = example(); units = deepcopy(UNIT_QUALIFICATION)
    if case == 'unsupported': raw['query_status'] = 'unsupported'
    if case == 'eligible': raw['eligible'] = True
    if case == 'units': units['ticks_per_second'] = 1
    if case == 'mod': raw['mods']['space-age'] = '2.0.77'
    if case == 'prototype': raw['prototypes'][0]['max_usage'] = 0
    if case == 'bool_rate': raw['prototypes'][0]['max_usage'] = True
    if case == 'unknown_load': next(e for e in raw['registry'] if e['unit'] == 1002)['name'] = 'solar-panel'
    if case == 'split_power': raw['poles'][-1]['network_id'] = 8
    if case == 'missing_pole': raw['poles'].pop()
    if case == 'asymmetric_wire': raw['poles'][0]['neighbors'] = []
    if case == 'missing_supply': raw['supply_surveys'] = [s for s in raw['supply_surveys'] if s['key'] != '2000']
    if case == 'missing_coverage': raw['supply_surveys'] = [s for s in raw['supply_surveys'] if s['key'] != 'unit:1001']
    if case == 'foreign_coverage': raw['supply_surveys'][0]['poles'].append(9999)
    if case == 'missing_engine_fluid': raw['fluid_members'] = [m for m in raw['fluid_members'] if m['unit'] != 1001]
    if case == 'missing_pipe_peer': raw['fluid_members'][0]['boxes'][0]['connections'][0]['unit'] = 99999
    if case == 'asymmetric_pipe': raw['fluid_members'][0]['boxes'][0]['connections'] = []
    if case == 'wrong_segment': raw['fluid_members'][0]['boxes'][0]['segment_id'] = 999
    if case == 'segment_capacity': raw['fluid_members'][0]['boxes'][0]['capacity'] = 401
    if case == 'hot_water': raw['fluid_members'][0]['boxes'][0]['temperature'] = 16
    if case == 'mixed_fluid': raw['fluid_members'][0]['boxes'][0]['fluid'] = 'steam'
    if case == 'double_boiler': next(e for e in raw['registry'] if e['unit'] == 1003)['name'] = 'boiler'
    if case == 'source_unit': raw['sources'][0]['target_unit'] = 999
    if case == 'source_geometry': raw['sources'][0]['drill_bounds']['left_top']['x'] += .1
    if case == 'resource_alias': raw['sources'][0]['resources'] *= 2
    if case == 'foreign_drill': raw['sources'][0]['neighbor_drills'] = [{'unit': 999, 'mining_area': raw['sources'][0]['mining_area']}]
    if case == 'wrong_fuel': raw['fuel_targets'][0]['burning'] = 'wood'
    if case == 'aliased_owner': raw['registry'][0]['unit'] = raw['registry'][1]['unit']
    if case == 'foreign_surface': raw['registry'][0]['surface_index'] = 2
    if case == 'unpriced_buffer': raw['electric_members'][0]['buffer_capacity'] = None
    if case == 'missing_witness': raw['buffer_witnesses'].pop()
    if case == 'foreign_witness': raw['buffer_witnesses'][0]['unit'] = 99999
    if case == 'unpriced_planned_buffer': raw['buffer_witnesses'][0]['capacity'] = None
    with pytest.raises(NativeEconomicsUnavailable): checked(raw, bundle, units)


def test_fractional_fuel_preserves_opposite_bootstrap_and_demand_rounding():
    raw, bundle = example()
    for target in raw['fuel_targets']:
        target.update(burning='coal', remaining_burning_fuel=1.2, heat=.3)
    result = checked(raw, bundle)
    assert result.power.boiler_stored_fuel_joules == 40_000_001
    assert result.sources[0].consumer_stored_fuel_joules_lower == 40_000_001
    assert result.sources[0].consumer_stored_fuel_joules_upper == 40_000_002


def test_query_missing_runtime_is_read_only_and_plain_unsupported():
    from lupa.lua52 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute('script={active_mods={base="2.0.77"}}; storage={sentinel=42}; '
                'helpers={table_to_json=function(v) projected=v;return "{}" end}; '
                'rcon={print=function(v) response=v end}')
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    assert lua.eval('jev_fle_runtime==nil and storage.sentinel==42')
    assert lua.eval('projected.query_status') == 'unsupported'
    assert lua.eval('projected.reason') == 'runtime_unavailable'


def lua_runtime(raw, bundle):
    """Explicit API doubles execute the entire raw query, not its callbacks."""
    from lupa.lua52 import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.globals().model = lua.table_from(raw, recursive=True)
    lua.globals().bundle = lua.table_from(bundle, recursive=True)
    lua.execute('''
        local function forbidden() error('runtime callback invoked') end
        script={active_mods={base='2.0.77'}};storage={unchanged=true}
        defines={wire_connector_id={pole_copper=5},entity_status={working=1,disabled_by_control_behavior=2,no_input_fluid=3}}
        surface={index=1};force={index=1}
        actor={valid=true,unit_number=model.epoch.actor_unit,surface=surface,force=force}
        local function inventory(rows)
            return {valid=true,get_contents=function() return rows end}
        end
        player={index=1,connected=true,character=actor,cheat_mode=false,
            crafting_queue_size=0,get_main_inventory=function() return inventory({}) end}
        game={tick=model.epoch.tick,speed=1,tick_paused=false,connected_players={player},
            get_player=function(index) return index==1 and player or nil end}
        campaign={entities={},observe=forbidden,
            connector_ledger={protocol=1,routes={},active=nil}};fair={actor=forbidden}
        jev_fle_runtime={campaign=campaign,fair=fair,agent_characters={actor},jev_bound_player_index=1,
            jev_player_index=1,jev_session_id=model.epoch.session_id,coal_supply={revision=4,committed=false,targets={},rows={}}}
        q=jev_fle_runtime.coal_supply
        prototypes={entity={},item={coal={fuel_value=4000000}},fluid={}}
        for _,row in ipairs(model.prototypes) do
            local p={get_max_energy_usage=function(quality) assert(quality=='normal');return row.max_usage end,
                get_max_energy_production=function(quality) assert(quality=='normal');return row.max_production end}
            if row.source=='electric' then p.electric_energy_source_prototype={drain=row.drain,buffer_capacity=row.buffer_capacity}
            elseif row.source=='burner' then p.burner_prototype={effectivity=row.burner_efficiency}
            elseif row.source=='void' then p.void_energy_source_prototype={} end
            p.target_temperature=row.target_temperature;p.boiler_mode=row.boiler_mode;p.effectivity=row.generator_efficiency
            p.maximum_temperature=row.maximum_temperature;p.mining_speed=row.mining_speed
            p.get_fluid_usage_per_tick=function(quality) assert(quality=='normal');return row.fluid_usage_per_tick end
            prototypes.entity[row.name]=p
        end
        for _,row in ipairs(model.fluid_prototypes) do prototypes.fluid[row.name]=row end
        for _,row in ipairs(model.pole_prototypes) do
            prototypes.entity[row.name]={get_supply_area_distance=function() return row.supply_radius end}
        end
        prototypes.entity.pipe={}
        all_entities={};by_unit={};resources={}
        for _,row in ipairs(model.registry) do
            local e={valid=true,unit_number=row.unit,name=row.name,quality={name=row.quality},
                position=row.position,bounding_box=row.bounds,surface=surface,force=force,
                prototype=prototypes.entity[row.name],consumption_bonus=0,productivity_bonus=0,
                get_module_inventory=function() return nil end,active=true,status=1,
                get_control_behavior=function() return nil end,
                electric_buffer_size=(row.name=='electric-mining-drill' or row.name=='inserter') and 1000 or nil}
            e.type=row.name=='boiler' and 'boiler' or row.name=='steam-engine' and 'generator'
                or row.name=='stone-furnace' and 'furnace' or row.name=='offshore-pump' and 'offshore-pump'
                or row.name=='substation' and 'electric-pole' or row.name=='lab' and 'lab' or 'pipe'
            campaign.entities[row.role]=e;by_unit[row.unit]=e;all_entities[#all_entities+1]=e
        end
        for _,row in ipairs(model.electric_members) do local e=by_unit[row.unit]
            e.electric_network_id=row.network_id;e.electric_buffer_size=row.buffer_capacity
            e.energy=row.energy;e.electric_drain=row.drain end
        for _,row in ipairs(model.buffer_witnesses) do
            by_unit[row.unit].electric_buffer_size=row.capacity
        end
        for _,row in ipairs(model.poles) do local e=by_unit[row.unit]
            e.electric_network_id=row.network_id
            e.get_wire_connector=function(id,create)
                assert(id==5 and create==false,'connector allocation requested')
                local wire={valid=true,is_ghost=false,real_connections={}}
                for _,unit in ipairs(row.neighbors) do wire.real_connections[#wire.real_connections+1]={target={owner=by_unit[unit]}} end
                return wire
            end
        end
        for _,row in ipairs(model.fuel_targets) do local e=by_unit[row.unit]
            e.burner={remaining_burning_fuel=row.remaining_burning_fuel,heat=row.heat}
            if row.burning~='' then e.burner.currently_burning={name={name=row.burning},quality={name='normal'}} end
            local inv={valid=true,get_contents=function()
                return row.coal>0 and {{name='coal',count=row.coal}} or {}
            end}
            if row.coal>0 then inv[1]={valid_for_read=true,name='coal',quality={name='normal'},count=row.coal} end
            e.get_fuel_inventory=function() return inv end
            if row.name=='stone-furnace' then
                e.get_output_inventory=function() return inventory({}) end
            end
        end
        for _,row in ipairs(model.sources) do
            local s=bundle[row.target];s.parts={};s.resources={}
            for _,r in ipairs(row.resources) do
                local e={valid=true,name=r.name,type='resource',position=r.position,amount=r.amount,
                    prototype={infinite_resource=false,resource_category='basic-solid',
                        mineable_properties={minable=true,mining_time=r.mining_time,
                            products={{name='coal',type='item',amount=1,probability=1}}}}}
                s.resources[#s.resources+1]=e;resources[#resources+1]=e
            end
            q.rows[row.target]=s;q.targets[#q.targets+1]=row.target
        end
        for _,row in ipairs(model.fluid_members) do local e=by_unit[row.unit];local boxes={}
            for _,f in ipairs(row.boxes) do
                boxes[f.index]={name=f.fluid,amount=f.amount,temperature=f.temperature}
            end
            boxes.get_prototype=function(i) return {filter=row.boxes[i].filter~='' and {name=row.boxes[i].filter} or nil} end
            boxes.get_capacity=function(i) return row.boxes[i].capacity end
            boxes.get_fluid_segment_id=function(i) return row.boxes[i].segment_id end
            boxes.get_pipe_connections=function(i)
                local result={}
                for _,edge in ipairs(row.boxes[i].connections) do
                    result[#result+1]={target={owner=by_unit[edge.unit]},target_fluidbox_index=edge.index}
                end
                return result
            end
            e.fluidbox=boxes
        end
        local function area(v)
            local a,b=v.left_top or v[1],v.right_bottom or v[2]
            return {x=a.x or a[1],y=a.y or a[2]},{x=b.x or b[1],y=b.y or b[2]}
        end
        surface.find_entities_filtered=function(options)
            local a,b=area(options.area);local result={}
            for _,e in ipairs(options.type=='resource' and resources or all_entities) do
                if (not options.type or e.type==options.type) then
                    local included
                    if e.bounding_box then local x,y=area(e.bounding_box)
                        included=x.x<b.x and a.x<y.x and x.y<b.y and a.y<y.y
                    else included=e.position.x>=a.x and e.position.x<b.x and e.position.y>=a.y and e.position.y<b.y end
                    if included then result[#result+1]=e end
                end
            end
            if options.limit and #result>options.limit then while #result>options.limit do table.remove(result) end end
            return result
        end
        surface.find_entity=function(name,position)
            for _,e in ipairs(all_entities) do
                if e.name==name and e.position.x==position.x and e.position.y==position.y then return e end
            end
            return nil
        end
        helpers={table_to_json=function(value) projected=value;return '{}' end}
        rcon={print=function(value) response=value end}
    ''')
    return lua


def plain(value):
    if not hasattr(value, 'items'): return value
    items = dict(value.items())
    if items and set(items) == set(range(1, len(items)+1)):
        return [plain(items[i]) for i in range(1, len(items)+1)]
    return {key: plain(item) for key, item in items.items()}


def test_full_raw_query_derives_supported_facts_without_callbacks_or_state_writes():
    raw, bundle = example(); lua = lua_runtime(raw, bundle)
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'observed', projected['reason']
    result = checked(projected, bundle)
    assert result.power.buffer_capacity_joules_upper == 12_008_000
    assert lua.eval('storage.unchanged and next(storage)=="unchanged"')
    assert lua.eval('#q.targets') == 2
    assert lua.eval('game.tick') == raw['epoch']['tick']


def test_fixed_query_projects_current_research_and_active_furnace_in_same_rpc():
    raw, bundle = example()
    next(row for row in raw['fuel_targets'] if row['role'] == 'furnace').update(
        burning='coal', remaining_burning_fuel=100)
    lua = lua_runtime(raw, bundle)
    lua.execute('''
        defines.inventory={furnace_source=1,lab_input=2}
        force.current_research={name='current-study',research_unit_count=30,
            research_unit_energy=30,
            research_unit_ingredients={{name='automation-science-pack',amount=1}},
            prototype={ignore_tech_cost_multiplier=false}}
        force.research_progress=.25
        game.difficulty_settings={technology_price_multiplier=1}
        local function inventory(rows)
            return {valid=true,get_contents=function() return rows end}
        end
        local lab=campaign.entities['utility:lab']
        lab.get_inventory=function(kind)
            assert(kind==2); return inventory({{name='automation-science-pack',count=2}})
        end
        local furnace=campaign.entities['furnace']
        furnace.get_inventory=function(kind)
            assert(kind==1); return inventory({{name='iron-ore',count=30}})
        end
        furnace.get_recipe=function() return {name='iron-plate',energy=3.2,
            ingredients={{type='item',name='iron-ore',amount=1}},
            products={{type='item',name='iron-plate',amount=1}}} end
        furnace.is_crafting=function() return true end
        furnace.crafting_progress=.5
        furnace.crafting_speed=1
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'observed', projected['reason']
    facts = checked(projected, bundle)
    work = facts.research_work
    assert work.technology == 'current-study' and work.progress == .25
    assert work.unit_count == 30 and work.cost_multiplier == 1
    assert work.unit_energy == 30
    assert work.ignore_cost_multiplier is False
    assert work.ingredients == (('automation-science-pack', 1),)
    assert work.lab_input == (('automation-science-pack', 2),)
    assert work.targets[0].role == 'furnace' and work.targets[0].recipe == 'iron-plate'
    assert work.targets[0].crafting and work.targets[0].burning == 'coal'
    assert work.targets[0].input == (('iron-ore', 30),)
    assert work.targets[0].recipe_ingredients == (('iron-ore', 1),)
    assert work.targets[0].recipe_products == (('iron-plate', 1),)
    assert work.targets[0].recipe_energy == 3.2 and work.targets[0].crafting_speed == 1
    assert facts.mutation_authorized is False and facts.native_payback_proven is False
    projected['research_work']['targets'][0]['unit'] = 999
    with pytest.raises(NativeEconomicsUnavailable, match='research_target_identity_mismatch'):
        checked(projected, bundle)


def test_fixed_query_binds_partial_actor_and_owned_stock_in_same_rpc():
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    lua.execute('''
        player.get_main_inventory=function() return {valid=true,get_contents=function()
            return {{name='automation-science-pack',count=8},{name='wood',count=2}}
        end} end
        player.crafting_queue_size=2
        campaign.entities.furnace.get_output_inventory=function()
            return {valid=true,get_contents=function() return {{name='iron-plate',count=11}} end}
        end
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'observed', projected['reason']
    facts = checked(projected, bundle)
    assert facts.material_scope.actor_items == (('automation-science-pack', 8), ('wood', 2))
    assert facts.material_scope.crafting_queue == 2
    assert facts.material_scope.owned_stock[0].output == (('iron-plate', 11),)
    assert facts.material_scope.closure_complete is False
    assert facts.mutation_authorized is False and facts.native_payback_proven is False


@pytest.mark.parametrize('change,reason', [
    (lambda r: r['material_scope'].update(closure_complete=True), 'invalid_material_scope'),
    (lambda r: r['material_scope'].update(actor_unit=999998), 'invalid_material_scope'),
    (lambda r: r['material_scope'].update(crafting_queue=-1), 'invalid_native_integer'),
    (lambda r: r['material_scope']['actor_items'].append({'name': 'coal', 'count': -1}), 'invalid_native_integer'),
    (lambda r: r['material_scope']['owned_stock'][0].update(unit=999), 'material_stock_owner_mismatch'),
    (lambda r: r['material_scope']['owned_stock'][0]['fuel'][0].update(count=9), 'material_fuel_stock_mismatch'),
    (lambda r: r['material_scope']['owned_stock'].pop(), 'invalid_native_array'),
])
def test_partial_material_scope_rejects_forged_closure_or_stock(change, reason):
    raw, bundle = example()
    change(raw)
    with pytest.raises(NativeEconomicsUnavailable, match=reason):
        checked(raw, bundle)


@pytest.mark.parametrize('mutation,reason', [
    ("player.crafting_queue_size=1001", 'material_crafting_queue'),
    ("campaign.entities.furnace.get_output_inventory=function() return nil end",
     'material_inventory_unavailable'),
    ("campaign.entities.furnace.get_fuel_inventory=function() return {valid=true,"
     "{valid_for_read=true,name='wood',quality={name='normal'},count=1},"
     "get_contents=function() return {{name='wood',count=1}} end} end", 'unsupported_fuel'),
])
def test_native_partial_material_scope_fails_closed_on_unsupported_state(mutation, reason):
    raw, bundle = example(); lua = lua_runtime(raw, bundle)
    lua.execute(mutation)
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'unsupported'
    assert projected['reason'] == reason
    with pytest.raises(NativeEconomicsUnavailable):
        checked(projected, bundle)


def test_selected_research_without_owned_lab_preserves_graph_projection():
    raw, bundle = example()
    raw['registry'] = [row for row in raw['registry'] if row['role'] != 'utility:lab']
    raw['electric_members'] = [row for row in raw['electric_members'] if row['unit'] != 1002]
    raw['supply_surveys'] = [row for row in raw['supply_surveys']
                             if not (row['kind'] == 'coverage' and row['key'] == 'unit:1002')]
    for row in raw['supply_surveys']:
        if row['kind'] == 'supply':
            row['members'] = [unit for unit in row['members'] if unit != 1002]
    lua = lua_runtime(raw, bundle)
    lua.execute('''
        force.current_research={name='current-study'}
        force.research_progress=.1
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'observed', projected['reason']
    assert projected['research_work'] == {'status': 'unavailable',
        'reason': 'research_lab_unowned', 'technology': 'current-study',
        'progress': .1, 'unit_count': 0, 'cost_multiplier': 0,
        'ignore_cost_multiplier': False, 'unit_energy': 0,
        'ingredients': {}, 'lab': {}, 'targets': {}}
    # This minimal early graph still misses the decoder's pre-existing two
    # electric-member bound; research absence itself does not fail the Lua query.


@pytest.mark.parametrize('mutation,reason', [
    ('force.current_research.research_unit_count=0', 'research_unit_count'),
    ('force.current_research.research_unit_energy=0', 'research_unit_count'),
    ('game.difficulty_settings.technology_price_multiplier=0', 'research_cost_setting'),
    ('force.current_research.prototype.ignore_tech_cost_multiplier=nil', 'research_cost_setting'),
    ("force.current_research.research_unit_ingredients[1].amount=0", 'research_bill_unsupported'),
    ("campaign.entities['furnace'].recipe.products[1].independent_probability=.5", 'research_bill_unsupported'),
    ("campaign.entities['furnace'].recipe.products[1].probability=.5", 'research_bill_unsupported'),
    ("campaign.entities['furnace'].recipe.products[1].shared_probability={group='x'}", 'research_bill_unsupported'),
    ("campaign.entities['furnace'].recipe.ingredients[1].type='fluid'", 'research_bill_kind'),
    ("campaign.entities['furnace'].recipe.energy=0", 'research_recipe_energy'),
    ("campaign.entities['furnace'].crafting_speed=0", 'research_crafting_speed'),
])
def test_native_research_bill_refuses_unsupported_forms(mutation, reason):
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    lua.execute('''
        defines.inventory={furnace_source=1,lab_input=2}
        force.current_research={name='current-study',research_unit_count=30,
            research_unit_energy=30,
            research_unit_ingredients={{name='automation-science-pack',amount=1}},
            prototype={ignore_tech_cost_multiplier=false}}
        force.research_progress=.25
        game.difficulty_settings={technology_price_multiplier=1}
        local inventory={valid=true,get_contents=function() return {} end}
        campaign.entities['utility:lab'].get_inventory=function() return inventory end
        local furnace=campaign.entities['furnace']
        furnace.get_inventory=function() return inventory end
        furnace.recipe={name='iron-plate',energy=3.2,
            ingredients={{type='item',name='iron-ore',amount=1}},
            products={{type='item',name='iron-plate',amount=1}}}
        furnace.get_recipe=function() return furnace.recipe end
        furnace.is_crafting=function() return true end
        furnace.crafting_progress=.5
        furnace.crafting_speed=1
    ''')
    lua.execute(mutation)
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'unsupported'
    assert projected['reason'] == reason


@pytest.mark.parametrize('cycle_enabled', [False, True], ids=['bridged-v5', 'closed-world-v6'])
def test_fixed_query_projects_source_bound_manual_rows_with_current_graph(cycle_enabled):
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    from jev_factorio.backends.native_attachment import (
        CLOSED_WORLD_PROFILE, MANUAL_CYCLE_PROFILE, connector_observer_bridge_sha256,
        cycle_journal_sha256)
    journal_hash = 'a' * 64
    lua.globals().manual_profile = CLOSED_WORLD_PROFILE if cycle_enabled else MANUAL_CYCLE_PROFILE
    lua.globals().journal_hash = journal_hash
    lua.globals().bridge_hash = connector_observer_bridge_sha256()
    lua.globals().cycle_hash = cycle_journal_sha256() if cycle_enabled else ''
    lua.globals().cycle_enabled = cycle_enabled
    lua.execute('''
        local j={protocol=1,session_id=jev_fle_runtime.jev_session_id,
            actor_index=1,actor_unit=actor.unit_number,surface_index=1,force_index=1,
            pending=nil,order={'gather-1'},rows={}}
        j.tick_handler=function() end
        j.rows['gather-1']={receipt='gather-1',status='complete',pending=false,
            overflow=false,fault=false,session_id=j.session_id,actor_index=1,
            actor_unit=j.actor_unit,surface_index=1,force_index=1,resource='coal',
            started_tick=9000,finished_tick=9100,coal_before=0,coal_after=5,
            walking_ticks=20,mining_ticks=30}
        jev_fle_runtime.coal_manual_journal_v1=j
        jev_fle_runtime.connector_observer_bridge_v1={protocol=1,
            snapshot_qualified=true,snapshot_tick=8999,
            snapshot_ownership={protocol=1,session_id=jev_fle_runtime.jev_session_id,
                tick=8999,routes={}}}
        local cycle=nil
        if cycle_enabled then
            cycle={protocol=2,combined_tick_handler=function() end}
            jev_fle_runtime.coal_manual_cycle_v2=cycle
        end
        local assets={coal_manual_journal_v1=journal_hash,
            connector_observer_bridge_v1=bridge_hash}
        if cycle then assets.coal_manual_cycle_v2=cycle_hash end
        jev_fle_runtime.native_installation={profile=manual_profile,
            callbacks={journal_tick=j.tick_handler,
                cycle_tick=cycle and cycle.combined_tick_handler or nil},
            assets=assets}
        campaign.receipt_order={'deliver-1','deliver-2'}
        campaign.receipts={
            ['deliver-1']={role='furnace',item='coal',quantity=2,
                unit_number=campaign.entities.furnace.unit_number,extracting=false,tick=9200},
            ['deliver-2']={role='utility:boiler',item='coal',quantity=3,
                unit_number=campaign.entities['utility:boiler'].unit_number,
                extracting=false,tick=9300}}
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'observed', projected['reason']
    facts = decode(projected, expected_epoch=raw['epoch'], expected_bundle=bundle,
                   unit_qualification=UNIT_QUALIFICATION,
                   expected_journal_asset_sha256=journal_hash)
    manual = facts.manual_cycle
    assert manual.journal_asset_sha256 == journal_hash
    assert manual.gathers[0].receipt == 'gather-1'
    assert [(row.role, row.coal) for row in manual.deliveries] == [
        ('furnace', 2), ('utility:boiler', 3)]
    assert manual.attempts_bound is False and manual.cycle_complete is False
    assert facts.native_payback_proven is False and facts.mutation_authorized is False
    with pytest.raises(NativeEconomicsUnavailable, match='manual_journal_source_mismatch'):
        decode(projected, expected_epoch=raw['epoch'], expected_bundle=bundle,
               unit_qualification=UNIT_QUALIFICATION,
               expected_journal_asset_sha256='b' * 64)
    projected['manual_cycle']['deliveries'][0]['unit'] = 999
    with pytest.raises(NativeEconomicsUnavailable, match='manual_delivery_owner_mismatch'):
        decode(projected, expected_epoch=raw['epoch'], expected_bundle=bundle,
               unit_qualification=UNIT_QUALIFICATION,
               expected_journal_asset_sha256=journal_hash)


@pytest.mark.parametrize('mutation', [
    "j.pending={receipt='in-flight'}",
    "j.rows['gather-1'].fault='lost-tick'",
    "j.rows['gather-1'].overflow=true",
    "j.rows['gather-1'].actor_unit=999",
    "j.rows['gather-1'].status='failed'",
    "table.insert(j.order,'missing')",
    "jev_fle_runtime.native_installation.callbacks.journal_tick=function() end",
    "jev_fle_runtime.native_installation.profile='retained-v4'",
    "jev_fle_runtime.native_installation.profile='e759-observation-v2-water-origin-v4-manual-cycle-v5-connector-observer-v1'",
])
def test_fixed_manual_query_refuses_pending_fault_rebound_or_unqualified_source(mutation):
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    lua.execute('''
        local j={protocol=1,session_id=jev_fle_runtime.jev_session_id,
            actor_index=1,actor_unit=actor.unit_number,surface_index=1,force_index=1,
            pending=nil,order={'gather-1'},rows={}}
        j.tick_handler=function() end
        j.rows['gather-1']={receipt='gather-1',status='complete',pending=false,
            overflow=false,fault=false,session_id=j.session_id,actor_index=1,
            actor_unit=j.actor_unit,surface_index=1,force_index=1,resource='coal',
            started_tick=9000,finished_tick=9100,coal_before=0,coal_after=5,
            walking_ticks=20,mining_ticks=30}
        jev_fle_runtime.coal_manual_journal_v1=j
        jev_fle_runtime.native_installation={
            profile='e759-observation-v2-water-origin-v4-manual-cycle-v5',
            callbacks={journal_tick=j.tick_handler},
            assets={coal_manual_journal_v1=string.rep('a',64)}}
        campaign.receipt_order={};campaign.receipts={}
    ''')
    lua.execute('local j=jev_fle_runtime.coal_manual_journal_v1; ' + mutation)
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'unsupported'
    assert projected['reason'] in {'manual_journal_unqualified', 'manual_journal_incomplete',
                                   'manual_journal_bound', 'manual_journal_receipt'}


def observed_research_fixture():
    raw, bundle = example()
    next(row for row in raw['fuel_targets'] if row['role'] == 'furnace')['burning'] = 'coal'
    furnace_unit = next(row for row in raw['registry'] if row['role'] == 'furnace')['unit']
    raw['research_work'] = {'status': 'observed', 'reason': 'current_research_activity',
        'technology': 'current-study', 'progress': .25, 'unit_count': 30,
        'cost_multiplier': 1, 'ignore_cost_multiplier': False, 'unit_energy': 30,
        'ingredients': [{'name': 'automation-science-pack', 'amount': 1}],
        'lab': {'role': 'utility:lab', 'unit': 1002,
                'input': [{'name': 'automation-science-pack', 'count': 2}]},
        'targets': [{'role': 'furnace', 'unit': furnace_unit,
                     'recipe': 'iron-plate', 'crafting': True, 'crafting_progress': .5,
                     'burning': 'coal', 'crafting_speed': 1, 'recipe_energy': 3.2,
                     'input': [{'name': 'iron-ore', 'count': 30}],
                     'recipe_ingredients': [{'name': 'iron-ore', 'amount': 1}],
                     'recipe_products': [{'name': 'iron-plate', 'amount': 1}]}]}
    return raw, bundle


@pytest.mark.parametrize('change', [
    lambda r: r['research_work']['lab'].update(unit=999),
    lambda r: r['research_work']['targets'].clear(),
    lambda r: r['research_work']['targets'][0].update(burning=''),
    lambda r: r['research_work']['targets'][0].update(recipe=''),
    lambda r: r['research_work']['targets'][0]['input'].append({'name': 'iron-ore', 'count': 1}),
    lambda r: r['research_work']['lab']['input'][0].update(count=-1),
    lambda r: r['research_work'].update(progress=1.1),
    lambda r: r['research_work'].update(unit_count=0),
    lambda r: r['research_work'].update(cost_multiplier=0),
    lambda r: r['research_work'].update(ignore_cost_multiplier='false'),
    lambda r: r['research_work']['ingredients'][0].update(amount=-1),
    lambda r: r['research_work']['targets'][0]['recipe_products'][0].update(name=''),
    lambda r: r['research_work']['targets'][0]['recipe_products'][0].update(probability=.5),
    lambda r: r['research_work']['targets'][0]['recipe_ingredients'].clear(),
])
def test_research_witness_rejects_missing_rebound_or_ambiguous_activity(change):
    raw, bundle = observed_research_fixture()
    assert checked(raw, bundle).research_work is not None
    change(raw)
    with pytest.raises(NativeEconomicsUnavailable):
        checked(raw, bundle)


def test_started_craft_without_current_burn_is_observational_only():
    raw, bundle = observed_research_fixture()
    next(row for row in raw['fuel_targets'] if row['role'] == 'furnace')['burning'] = ''
    raw['research_work']['targets'][0]['burning'] = ''
    facts = checked(raw, bundle)
    assert facts.research_work.targets[0].crafting is True
    assert facts.research_work.targets[0].burning == ''
    assert facts.native_payback_proven is False
    assert facts.mutation_authorized is False


def paid_connector_runtime():
    raw, bundle = example()
    lua = lua_runtime(raw, bundle)
    receipt = 'a' * 64
    lua.globals().connector_receipt = receipt
    lua.execute('''
        local position={x=1000.5,y=1000.5}
        local e={valid=true,unit_number=9000,name='pipe',quality={name='normal'},
            position=position,bounding_box={left_top={x=1000.1,y=1000.1},
                right_bottom={x=1000.9,y=1000.9}},surface=surface,force=force,
            prototype=prototypes.entity.pipe,type='pipe'}
        by_unit[9000]=e;all_entities[#all_entities+1]=e
        campaign.connector_ledger.routes[connector_receipt]={id=connector_receipt,
            source='utility:boiler',target='utility:engine',
            source_unit=campaign.entities['utility:boiler'].unit_number,
            target_unit=campaign.entities['utility:engine'].unit_number,
            actor_unit=actor.unit_number,surface_index=surface.index,
            force_index=force.index,session_id=jev_fle_runtime.jev_session_id,
            state='complete',owned=true,paid=1,external=0,
            kind='pipe',fluid='steam',pending=nil,cells={{paid=1,external=false,
                unit_number=9000,position=position}}}
    ''')
    role = 'connector:' + receipt + ':1'
    expected = {role: {'unit': 9000, 'name': 'pipe',
                       'position': {'x': 1000.5, 'y': 1000.5}}}
    return lua, bundle, expected


def test_query_binds_paid_connector_outside_registered_factory_roles():
    lua, bundle, expected = paid_connector_runtime()
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'observed', projected['reason']
    result = decode(projected, expected_epoch=projected['epoch'],
                    expected_bundle=bundle, unit_qualification=UNIT_QUALIFICATION,
                    expected_connectors=expected,
                    expected_routes={row['id']: row for row in projected['connector_routes']})
    assert result.mutation_authorized is False
    assert any(row['role'] in expected for row in projected['registry'])


def test_query_rejects_paid_route_rebound_to_another_owned_endpoint():
    lua, bundle, expected = paid_connector_runtime()
    original = {'id': 'a' * 64, 'source': 'utility:boiler', 'target': 'utility:engine',
                'source_unit': lua.eval("campaign.entities['utility:boiler'].unit_number"),
                'target_unit': lua.eval("campaign.entities['utility:engine'].unit_number"),
                'kind': 'pipe', 'fluid': 'steam', 'actor_unit': lua.eval('actor.unit_number'),
                'session_id': lua.eval('jev_fle_runtime.jev_session_id'),
                'surface_index': 1, 'force_index': 1, 'state': 'complete',
                'owned': True, 'paid': 1, 'cell_count': 1}
    lua.execute("local row=campaign.connector_ledger.routes[connector_receipt]; "
                "row.source='pole:2000'; row.source_unit=campaign.entities['pole:2000'].unit_number")
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'observed'
    with pytest.raises(NativeEconomicsUnavailable, match='connector_route_binding_mismatch'):
        decode(projected, expected_epoch=projected['epoch'], expected_bundle=bundle,
               unit_qualification=UNIT_QUALIFICATION, expected_connectors=expected,
               expected_routes={original['id']: original})


@pytest.mark.parametrize('mutation', [
    "campaign.connector_ledger.routes[connector_receipt].state='building'",
    "campaign.connector_ledger.routes[connector_receipt].state='fault'",
    "campaign.connector_ledger.routes[connector_receipt].paid=0",
    "campaign.connector_ledger.routes[connector_receipt].external=1",
    "campaign.connector_ledger.routes[connector_receipt].actor_unit=2",
    "campaign.connector_ledger.routes[connector_receipt].cells[1].unit_number=9001",
    "campaign.connector_ledger.active=connector_receipt",
    "campaign.connector_ledger.routes[connector_receipt].pending=1",
])
def test_query_refuses_unpaid_or_changed_connector_graph(mutation):
    lua, bundle, _ = paid_connector_runtime()
    lua.execute(mutation)
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'unsupported'
    with pytest.raises(NativeEconomicsUnavailable):
        checked(projected, bundle)


def test_query_refuses_more_than_128_paid_connector_cells():
    lua, bundle, _ = paid_connector_runtime()
    lua.execute('''
        local row=campaign.connector_ledger.routes[connector_receipt]
        for index=2,129 do
            local position={x=1000.5+index,y=1000.5}
            local unit=9000+index
            local e={valid=true,unit_number=unit,name='pipe',quality={name='normal'},
                position=position,bounding_box={left_top={x=position.x-.4,y=position.y-.4},
                    right_bottom={x=position.x+.4,y=position.y+.4}},
                surface=surface,force=force,prototype=prototypes.entity.pipe,type='pipe'}
            by_unit[unit]=e;all_entities[#all_entities+1]=e
            row.cells[index]={paid=1,external=false,unit_number=unit,position=position}
        end
        row.paid=129
    ''')
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'unsupported'
    assert projected['reason'] == 'survey_bound'
    with pytest.raises(NativeEconomicsUnavailable):
        checked(projected, bundle)


@pytest.mark.parametrize('mutation', [
    "campaign.entities['pole:2000']=nil",
    "campaign.entities['utility:water']=nil",
    "q.targets[5]='extra'",
    "q.rows.furnace.resources={}",
    "script.active_mods.foreign='1.0'",
    "by_unit[1001].electric_network_id=9",
    "by_unit[1002].consumption_bonus=.5",
    "by_unit[1001].active=false",
    "by_unit[1003].active=false",
    "campaign.entities['utility:boiler'].active=false",
    "by_unit[1001].status=2",
    "by_unit[1001].status=3",
    "by_unit[1003].get_control_behavior=function() return {} end",
    "by_unit[1004].electric_buffer_size=nil",
    "by_unit[1006].electric_buffer_size=2000",
    "prototypes.entity.lab.get_max_energy_usage=function() error('unknown API detail') end",
])
def test_raw_query_rejects_unowned_changed_or_unknown_native_graph(mutation):
    raw, bundle = example(); lua = lua_runtime(raw, bundle)
    lua.execute(mutation)
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    projected = plain(lua.globals().projected)
    assert projected['query_status'] == 'unsupported'
    assert projected['reason'].replace('_', '').isalpha()
    with pytest.raises(NativeEconomicsUnavailable): checked(projected, bundle)


def test_raw_query_output_budget_never_publishes_partial_success():
    raw, bundle = example(); lua = lua_runtime(raw, bundle)
    lua.execute("encode_calls=0;helpers.table_to_json=function(v) encode_calls=encode_calls+1;projected=v;"
                "return encode_calls==1 and string.rep('x',262145) or '{}' end")
    lua.execute(files('jev_factorio').joinpath('lua/coal_economics.lua').read_text())
    assert plain(lua.globals().projected) == {'schema': SCHEMA, 'query_status': 'unsupported', 'reason': 'response_bound'}
    assert lua.eval('#response') == 2


@pytest.mark.parametrize('field', ['poles', 'fluid_members', 'electric_members', 'fuel_targets', 'sources'])
def test_native_identity_bool_aliases_are_rejected(field):
    raw, bundle = example()
    raw[field][0]['target_unit' if field == 'sources' else 'unit'] = True
    with pytest.raises(NativeEconomicsUnavailable): checked(raw, bundle)


@pytest.mark.parametrize('family', ['electric_members', 'fluid_members', 'fuel_targets'])
@pytest.mark.parametrize('field,value', [('active', False), ('active', 1),
    ('control_behavior_present', True), ('status', 'disabled_by_control_behavior')])
def test_decoder_rejects_inactive_or_controlled_operation(family, field, value):
    raw, bundle = example()
    raw[family][0]['operation'][field] = value
    with pytest.raises(NativeEconomicsUnavailable): checked(raw, bundle)
