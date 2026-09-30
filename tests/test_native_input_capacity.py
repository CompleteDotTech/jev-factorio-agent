"""Same-command receiver-capacity evidence; these tests do not start Factorio."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from jev_factorio.backends.native_input_capacity import (
    MARKER, decode, observation_command, source_sha256,
)


def sample():
    return {
        'session_id': 'campaign-1', 'tick': 1234, 'actor_unit': 2543,
        'surface_index': 1, 'force_index': 1,
        'inventory': {'coal': 2, 'iron-ore': 3},
        'factory': {
            'entities': {'recipe:iron-plate': {
                'name': 'stone-furnace', 'unit_number': 2547,
            }},
            'production_sites': {
                'protocol': 1, 'session_id': 'campaign-1', 'tick': 1234,
                'sources': {'recipe:iron-plate': {
                    'state': 'owned', 'source_unit': 2547,
                }},
            },
        },
    }


def payload(snapshot=None):
    snapshot = snapshot or sample()
    return {
        'schema': 1, 'tick': snapshot['tick'],
        'session_id': snapshot['session_id'], 'actor_unit': snapshot['actor_unit'],
        'surface_index': snapshot['surface_index'], 'force_index': snapshot['force_index'],
        'actor_inventory': deepcopy(snapshot['inventory']), 'complete': True,
        'eligible_count': 1, 'item_count': 2,
        'receivers': {'recipe:iron-plate': {
            'unit_number': 2547, 'name': 'stone-furnace', 'type': 'furnace',
            'burner': True, 'surface_index': 1, 'force_index': 1,
            'items': {
                'coal': {'inventory': 'fuel', 'actor_count': 2,
                         'insertable_count': 48, 'method': 'get_insertable_count'},
                'iron-ore': {'inventory': 'furnace_source', 'actor_count': 3,
                             'insertable_count': 50, 'method': 'get_insertable_count'},
            },
        }},
    }


def raw(value):
    import json
    return 'JEV_SNAPSHOT|{}\n{}{}'.format(
        '{}', MARKER, json.dumps(value, separators=(',', ':')))


def catalog():
    return NS(machines={'stone-furnace': {'burner': True}})


def test_one_read_command_calls_existing_snapshot_then_owned_role_capacity():
    native = NS(_discovery_epoch=4, _coherent_drill=51,
                backend=NS(_drill=None),
                catalog=NS(machines={'stone-furnace': {'burner': True}}))
    command = observation_command(native)
    assert command.startswith('local storage=jev_fle_runtime')
    assert command.count('observation_snapshot_v2(') == 1
    assert command.index('observation_snapshot_v2(') < command.index('storage.campaign.entities')
    assert 'storage.campaign.entities' in command
    assert 'game.get_entity_by_unit_number' not in command
    assert 'get_insertable_count' in command
    assert 'factory_insert' not in command and 'player.walking_state' not in command
    assert 'game.tick==game_before' in command
    receiver_guard = 'entity.surface.index~=surface or entity.force.index~=force'
    assert command.count(receiver_guard) >= 2
    assert command.rindex(receiver_guard) < command.index('get_insertable_count')


def test_decoder_binds_capacity_to_exact_current_inventory_and_owned_receiver():
    snapshot = sample()
    decoded = decode(raw(payload(snapshot)), snapshot, catalog())
    assert decoded['complete'] is True
    assert decoded['query_source_sha256'] == source_sha256()
    assert len(decoded['query_source_sha256']) == 64
    assert decoded['receivers']['recipe:iron-plate']['items']['iron-ore'] == 50
    assert decoded['session_id'] == snapshot['session_id']
    assert decoded['tick'] == snapshot['tick']


@pytest.mark.parametrize(('where', 'key', 'value'), [
    ('payload', 'tick', 1235),
    ('payload', 'session_id', 'other'),
    ('payload', 'actor_unit', 999),
    ('payload', 'surface_index', 2),
    ('payload', 'force_index', 2),
    ('payload', 'actor_inventory', {'coal': 2, 'iron-ore': 4}),
    ('receiver', 'unit_number', 999),
    ('receiver', 'name', 'steel-furnace'),
    ('receiver', 'type', 'assembling-machine'),
    ('receiver', 'surface_index', 2),
    ('receiver', 'force_index', 2),
    ('receiver', 'burner', False),
    ('source', 'source_unit', 999),
    ('production', 'tick', 1233),
])
def test_decoder_rejects_stale_or_foreign_identity(where, key, value):
    snapshot = sample()
    candidate = payload(snapshot)
    if where == 'payload':
        candidate[key] = value
    elif where == 'receiver':
        candidate['receivers']['recipe:iron-plate'][key] = value
    elif where == 'source':
        snapshot['factory']['production_sites']['sources']['recipe:iron-plate'][key] = value
    else:
        snapshot['factory']['production_sites'][key] = value
    with pytest.raises(ValueError):
        decode(raw(candidate), snapshot, catalog())


@pytest.mark.parametrize(('field', 'value'), [
    ('inventory', 'fuel'),
    ('method', 'get_contents'),
    ('actor_count', 2),
    ('insertable_count', -1),
])
def test_decoder_rejects_invalid_item_selector_counts_and_method(field, value):
    snapshot = sample()
    candidate = payload(snapshot)
    candidate['receivers']['recipe:iron-plate']['items']['iron-ore'][field] = value
    with pytest.raises(ValueError):
        decode(raw(candidate), snapshot, catalog())


def test_decoder_returns_unknown_when_native_query_exceeds_coverage_bounds():
    snapshot = sample()
    candidate = payload(snapshot)
    candidate.update(complete=False, eligible_count=65, receivers={})
    assert decode(raw(candidate), snapshot, catalog()) is None


def test_decoder_rejects_duplicate_sidecar_lines():
    snapshot = sample()
    line = raw(payload(snapshot)).splitlines()[1]
    with pytest.raises(ValueError, match='Ambiguous'):
        decode('JEV_SNAPSHOT|{}\n' + line + '\n' + line, snapshot, catalog())
