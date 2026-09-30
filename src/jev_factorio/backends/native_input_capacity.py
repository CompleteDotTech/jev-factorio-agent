"""One-command, same-tick receiver insertable-capacity evidence.

The installed v5 observation callback remains untouched. This fixed source-built
query invokes it once, then reads insertable counts from the same registered
campaign entities and RCON command. It is advisory evidence only: transfer
dispatch still checks native capacity immediately before removing inventory.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..iteration_timing import span

MARKER = 'JEV_RECEIVER_INPUT_CAPACITY|'
SCHEMA = 1
MAX_ROLES = 64
MAX_ITEMS = 64
MAX_PAIRS = 2048
MAX_INSERTABLE = 2**32 - 1
ITEM_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,127}$')
SUPPORTED_RECEIVER_TYPES = {
    'stone-furnace': 'furnace', 'steel-furnace': 'furnace',
    'electric-furnace': 'furnace',
    'assembling-machine-1': 'assembling-machine',
    'assembling-machine-2': 'assembling-machine',
    'assembling-machine-3': 'assembling-machine',
    'rocket-silo': 'rocket-silo',
}


def source_sha256() -> str:
    """Pin the source that builds this fixed query and its decoder."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _literal(value: Any) -> str:
    """Encode only bounded primitive values as Lua literals."""
    if value is None:
        return 'nil'
    if type(value) is int and 0 <= value <= 2**53 - 1:
        return str(value)
    if type(value) in {int, float}:
        return json.dumps(value, allow_nan=False)
    raise ValueError('Invalid native observation command argument')


def _position_literal(value: dict | None) -> str:
    if value is None:
        return 'nil'
    if (not isinstance(value, dict) or set(value) != {'x', 'y'}
            or any(type(part) not in {int, float} for part in value.values())):
        raise ValueError('Invalid native observation position')
    return '{x=' + _literal(value['x']) + ',y=' + _literal(value['y']) + '}'


def observation_command(native: Any) -> str:
    """Build one ordinary read command: the v5 snapshot plus fixed sidecar."""
    epoch = getattr(native, '_discovery_epoch', None)
    if type(epoch) is not int or epoch < 0:
        raise ValueError('Invalid native observation epoch')
    backend = native.backend
    drill = getattr(backend, '_drill', None)
    prior_drill = getattr(native, '_coherent_drill', None)
    if prior_drill is None and getattr(drill, 'unit_number', None) is not None:
        prior_drill = drill.unit_number
    if prior_drill is not None and (type(prior_drill) is not int or prior_drill <= 0):
        raise ValueError('Invalid native observation drill identity')
    prior_position = None
    if prior_drill is not None and drill is not None and drill.unit_number == prior_drill:
        position = getattr(drill, 'position', None)
        if position is not None:
            prior_position = {'x': position.x, 'y': position.y}
    call = ('storage.campaign.observation_snapshot_v2(' + str(epoch) + ','
            + _literal(prior_drill) + ',' + _position_literal(prior_position) + ')')
    supported = getattr(native.catalog, 'machines', None)
    if not isinstance(supported, dict) or len(supported) > 128:
        # Receiver capacity is advisory. An older adapter or an oversized
        # catalog still gets the qualified primary snapshot; it simply cannot
        # request this optional sidecar.
        return call
    supported_names = sorted(name for name in supported
                             if isinstance(name, str) and name in SUPPORTED_RECEIVER_TYPES)
    if not supported_names:
        return call
    encoded_names = json.dumps(supported_names, separators=(',', ':'))
    # The sidecar follows the existing callback within this one /sc. It uses
    # campaign role ownership directly; global unit lookup is not reliable for
    # all valid furnace entities in Factorio 2.0.
    return f'''local storage=jev_fle_runtime
local game_before=game.tick
local player=assert(storage.fair.actor())
local actor=assert(player.character)
local session=assert(storage.jev_session_id)
local surface=actor.surface.index
local force=actor.force.index
local actor_unit=actor.unit_number
local inventory=assert(player.get_main_inventory())
local inventory_counts={{}}
for _,stack in pairs(inventory.get_contents()) do
    assert(type(stack.name)=="string" and #stack.name>0 and #stack.name<=128)
    assert(type(stack.count)=="number" and stack.count>=1 and stack.count%1==0
        and stack.count<=9007199254740991)
    inventory_counts[stack.name]=(inventory_counts[stack.name] or 0)+stack.count
end
{call}
assert(game.tick==game_before and actor.valid and player.character==actor
    and storage.jev_session_id==session and actor.unit_number==actor_unit
    and actor.surface.index==surface and actor.force.index==force,
    "Native receiver capacity crossed observation identity")
local supported_names=helpers.json_to_table({json.dumps(encoded_names)})
local supported={{}}
for _,name in pairs(supported_names) do supported[name]=true end
local eligible={{}}
local role_identity_matches=true
for role,entity in pairs(storage.campaign.entities) do
    if type(role)=="string" and role:sub(1,7)=="recipe:" and entity.valid
        and supported[entity.name]
        and (entity.type=="furnace" or entity.type=="assembling-machine"
            or entity.type=="rocket-silo") then
        if entity.surface.index~=surface or entity.force.index~=force then
            role_identity_matches=false
        end
        table.insert(eligible,{{role=role,entity=entity}})
    end
end
table.sort(eligible,function(a,b) return a.role<b.role end)
local items={{}}
for name,count in pairs(inventory_counts) do
    if count>0 then table.insert(items,{{name=name,count=count}}) end
end
table.sort(items,function(a,b) return a.name<b.name end)
local complete=role_identity_matches and #eligible<={MAX_ROLES} and #items<={MAX_ITEMS}
    and #eligible*#items<={MAX_PAIRS}
local receivers={{}}
if complete then
    for _,entry in ipairs(eligible) do
        local entity=entry.entity
        -- Recheck actor identity at the point of each receiver query. The
        -- campaign role/source read above is ownership evidence, but it does
        -- not authorize capacity reads from a machine on another surface or
        -- force.
        if not entity.valid or entity.surface.index~=surface or entity.force.index~=force then
            complete=false
            break
        end
        local row={{unit_number=entity.unit_number,name=entity.name,
            type=entity.type,burner=entity.burner~=nil,
            surface_index=entity.surface.index,force_index=entity.force.index,items={{}}}}
        receivers[entry.role]=row
        for _,item in ipairs(items) do
            local selector,selector_name
            if item.name=="coal" and entity.burner then
                selector,selector_name=defines.inventory.fuel,"fuel"
            elseif entity.type=="furnace" then
                selector,selector_name=defines.inventory.furnace_source,"furnace_source"
            elseif entity.type=="assembling-machine" or entity.type=="rocket-silo" then
                selector,selector_name=defines.inventory.assembling_machine_input,
                    "assembling_machine_input"
            elseif entity.type=="lab" then
                selector,selector_name=defines.inventory.lab_input,"lab_input"
            elseif entity.burner then
                selector,selector_name=defines.inventory.fuel,"fuel"
            end
            if not selector then complete=false;break end
            local target=entity.get_inventory(selector)
            if not target or not target.valid then complete=false;break end
            local ok,count=pcall(function() return target.get_insertable_count(item.name) end)
            if not ok or type(count)~="number" or count<0 or count%1~=0
                or count>{MAX_INSERTABLE} then complete=false;break end
            row.items[item.name]={{inventory=selector_name,actor_count=item.count,insertable_count=count,
                method="get_insertable_count"}}
        end
        if not complete then break end
    end
end
assert(game.tick==game_before and actor.valid and player.character==actor
    and storage.jev_session_id==session and actor.unit_number==actor_unit
    and actor.surface.index==surface and actor.force.index==force,
    "Native receiver capacity crossed observation identity")
local payload={{schema={SCHEMA},tick=game_before,session_id=session,
    actor_unit=actor_unit,surface_index=surface,force_index=force,
    actor_inventory=inventory_counts,complete=complete,eligible_count=#eligible,
    item_count=#items,receivers=receivers}}
rcon.print("{MARKER}"..helpers.table_to_json(payload))'''


def _integer(value: Any, label: str, maximum: int = 2**53 - 1) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f'Invalid native receiver capacity {label}')
    return value


def _mapping(value: Any, label: str, maximum: int = 4096) -> dict:
    if value == []:
        value = {}
    if not isinstance(value, dict) or len(value) > maximum:
        raise ValueError(f'Invalid native receiver capacity {label}')
    return value


def _payload(raw: str) -> dict | None:
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 8 * 1024 * 1024:
        raise ValueError('Invalid bounded receiver capacity response')
    rows = [line.removeprefix(MARKER) for line in raw.splitlines()
            if line.startswith(MARKER)]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('Ambiguous native receiver capacity response')
    with span('native_decode'):
        value = json.loads(rows[0])
    required = {'schema', 'tick', 'session_id', 'actor_unit', 'surface_index',
                'force_index', 'actor_inventory', 'complete', 'eligible_count',
                'item_count', 'receivers'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('Invalid native receiver capacity envelope')
    return value


def decode(raw: str, snapshot: dict, catalog: Any) -> dict | None:
    """Validate sidecar identity, full bounded coverage, and exact insertable counts."""
    value = _payload(raw)
    if value is None:
        return None
    if (type(value['schema']) is not int or value['schema'] != SCHEMA
            or type(value['complete']) is not bool
            or value['session_id'] != snapshot.get('session_id')
            or value['tick'] != snapshot.get('tick')
            or value['actor_unit'] != snapshot.get('actor_unit')
            or value['surface_index'] != snapshot.get('surface_index')
            or value['force_index'] != snapshot.get('force_index')):
        raise ValueError('Native receiver capacity identity changed')
    tick = _integer(value['tick'], 'tick')
    for key in ('actor_unit', 'surface_index', 'force_index'):
        _integer(value[key], key, 2**53 - 1)
        if value[key] == 0:
            raise ValueError('Invalid native receiver capacity identity')
    actor_inventory = _mapping(value['actor_inventory'], 'actor inventory')
    for item, count in actor_inventory.items():
        if not isinstance(item, str) or not ITEM_NAME.fullmatch(item):
            raise ValueError('Invalid native receiver capacity item')
        _integer(count, 'actor count')
        if count == 0:
            raise ValueError('Invalid native receiver capacity actor count')
    if actor_inventory != _mapping(snapshot.get('inventory'), 'snapshot inventory'):
        raise ValueError('Native receiver capacity actor inventory changed')
    eligible_count = _integer(value['eligible_count'], 'role count')
    item_count = _integer(value['item_count'], 'item count')
    if not value['complete']:
        return None
    if eligible_count > MAX_ROLES or item_count > MAX_ITEMS:
        raise ValueError('Invalid complete native receiver capacity bounds')
    positive_items = {name: count for name, count in actor_inventory.items() if count > 0}
    if item_count != len(positive_items) or eligible_count * item_count > MAX_PAIRS:
        raise ValueError('Incomplete native receiver capacity bounds')
    factory = snapshot.get('factory')
    entities = factory.get('entities') if isinstance(factory, dict) else None
    if not isinstance(entities, dict):
        raise ValueError('Native receiver capacity has no factory identities')
    machine_names = catalog.machines
    expected = {
        role: entity for role, entity in entities.items()
        if isinstance(role, str) and role.startswith('recipe:')
        and isinstance(entity, dict) and entity.get('name') in machine_names
        and entity.get('name') in SUPPORTED_RECEIVER_TYPES
    }
    receivers = _mapping(value['receivers'], 'receivers', MAX_ROLES)
    if set(receivers) != set(expected) or eligible_count != len(expected):
        raise ValueError('Native receiver capacity role coverage changed')
    production = factory.get('production_sites')
    sources = production.get('sources') if isinstance(production, dict) else None
    if (not isinstance(production, dict)
            or type(production.get('protocol')) is not int or production['protocol'] != 1
            or production.get('session_id') != value['session_id']
            or type(production.get('tick')) is not int or production['tick'] != tick
            or not isinstance(sources, dict)):
        raise ValueError('Native receiver capacity ownership is stale')
    decoded: dict[str, dict] = {}
    for role, receiver in receivers.items():
        if not isinstance(role, str) or not role.startswith('recipe:'):
            raise ValueError('Invalid native receiver capacity role')
        entity = expected[role]
        machine = catalog.machines[entity['name']]
        expected_type = SUPPORTED_RECEIVER_TYPES[entity['name']]
        if (not isinstance(receiver, dict) or set(receiver) != {
                'unit_number', 'name', 'type', 'burner', 'surface_index',
                'force_index', 'items'}
                or type(receiver['unit_number']) is not int or receiver['unit_number'] <= 0
                or receiver['unit_number'] != entity.get('unit_number')
                or receiver['name'] != entity.get('name')
                or receiver['type'] != expected_type
                or _integer(receiver['surface_index'], 'source surface') != value['surface_index']
                or _integer(receiver['force_index'], 'source force') != value['force_index']
                or type(receiver['burner']) is not bool
                or receiver['burner'] is not (machine.get('burner') is True)
                or receiver['type'] not in {'furnace', 'assembling-machine', 'rocket-silo'}):
            raise ValueError('Native receiver capacity source identity changed')
        source = sources.get(role) if isinstance(sources, dict) else None
        if (not isinstance(source, dict) or source.get('state') != 'owned'
                or source.get('source_unit') != receiver['unit_number']):
            raise ValueError('Native receiver capacity source is not currently owned')
        items = _mapping(receiver['items'], 'receiver items', MAX_ITEMS)
        if set(items) != set(positive_items):
            raise ValueError('Native receiver capacity item coverage changed')
        accepted = {}
        for item, row in items.items():
            expected_inventory = ('fuel' if item == 'coal' and receiver['burner']
                                  else 'furnace_source' if receiver['type'] == 'furnace'
                                  else 'assembling_machine_input')
            if (not isinstance(item, str) or not ITEM_NAME.fullmatch(item)
                    or not isinstance(row, dict) or set(row) != {
                        'inventory', 'actor_count', 'insertable_count', 'method'}
                    or row.get('inventory') != expected_inventory
                    or row.get('method') != 'get_insertable_count'
                    or _integer(row.get('actor_count'), 'actor count') != positive_items[item]):
                raise ValueError('Invalid native receiver capacity sample')
            accepted[item] = _integer(row.get('insertable_count'), 'insertable count', MAX_INSERTABLE)
        decoded[role] = {
            'unit_number': receiver['unit_number'], 'name': receiver['name'],
            'type': receiver['type'], 'burner': receiver['burner'],
            'surface_index': receiver['surface_index'],
            'force_index': receiver['force_index'],
            'items': accepted,
        }
    return {
        'schema': SCHEMA, 'tick': tick, 'session_id': value['session_id'],
        'actor_unit': value['actor_unit'], 'surface_index': value['surface_index'],
        'force_index': value['force_index'], 'method': 'get_insertable_count',
        'query_source_sha256': source_sha256(),
        'complete': True, 'receivers': decoded,
        'basis': 'same_rpc_owned_campaign_receiver_capacity',
    }
