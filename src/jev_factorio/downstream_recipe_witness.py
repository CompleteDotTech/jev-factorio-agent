"""Strict read-only decoder for one owned downstream native recipe edge.

The witness proves a current deterministic recipe dependency, not which stock
was consumed or any science-flow acceptance outcome.
"""
from __future__ import annotations

import json
import re
from importlib.resources import files

from .backends.native_attachment import (
    MANUAL_CYCLE_PROFILE, WATER_ORIGIN_OBSERVATION_PROFILE, readback, require_asset,
)
from .iteration_timing import decode_native

SCHEMA = 'jev.downstream-recipe-witness.v1'
SOURCE = 'lua/downstream_recipe_witness.lua'
REQUEST_KEYS = frozenset({'route', 'producer_role', 'producer_unit', 'product_item',
                          'consumer_role', 'consumer_unit', 'science_pack'})
ROOT_KEYS = frozenset({'schema', 'status', 'reason', 'base_version', 'epoch',
                       'request', 'route', 'producer', 'consumer',
                       'recipe_dependency_verified', 'stock_provenance_qualified',
                       'mutation_authorized'})
EPOCH_KEYS = frozenset({'session_id', 'tick', 'actor_index', 'actor_unit',
                        'surface_index', 'force_index'})
ROUTE_KEYS = frozenset({'id', 'item', 'source_role', 'source_unit', 'target_role',
                        'target_unit', 'paid_parts'})
RECIPE_KEYS = frozenset({'name', 'product', 'product_amount', 'ingredients'})
PACKS = frozenset({'automation-science-pack', 'logistic-science-pack',
                   'chemical-science-pack', 'military-science-pack', 'production-science-pack',
                   'utility-science-pack', 'space-science-pack'})
REASONS = frozenset({'unsupported', 'malformed', 'bound', 'request', 'native_version',
                     'installed_source', 'actor', 'epoch', 'route', 'identity',
                     'producer', 'recipe', 'dependency'})
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9:_.-]*\Z')


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise ValueError(code)


def _name(value, maximum=128) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= maximum and _NAME.fullmatch(value) is not None


def _integer(value, minimum=1, maximum=9007199254740991) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _attachment(attachment: dict) -> None:
    _need(isinstance(attachment, dict) and isinstance(attachment.get('modules'), dict),
          'invalid_attachment')
    _need(require_asset(attachment, 'solid_routes') is True, 'invalid_attachment')
    manifest = attachment.get('native_installation')
    _need(isinstance(manifest, dict) and manifest.get('profile') in {
        WATER_ORIGIN_OBSERVATION_PROFILE, MANUAL_CYCLE_PROFILE}, 'invalid_attachment')
    _need(_name(attachment.get('session_id')) and _integer(attachment.get('actor_unit')),
          'invalid_attachment')


def validate_request(value: dict) -> dict:
    _need(isinstance(value, dict) and set(value) == REQUEST_KEYS, 'invalid_request')
    _need(_name(value['route'], 256) and all(_name(value[key]) for key in
          ('producer_role', 'product_item', 'consumer_role', 'science_pack'))
          and _integer(value['producer_unit']) and _integer(value['consumer_unit'])
          and value['producer_role'] != value['consumer_role']
          and value['producer_unit'] != value['consumer_unit']
          and value['science_pack'] in PACKS, 'invalid_request')
    return dict(value)


def command(request: dict, attachment: dict) -> str:
    """Build a read-only query after a separately qualified attachment readback."""
    request = validate_request(request)
    _attachment(attachment)
    source = files('jev_factorio').joinpath(SOURCE).read_text(encoding='utf-8')
    return '/sc local request=helpers.json_to_table(' + json.dumps(
        json.dumps(request, sort_keys=True, separators=(',', ':'))) + '); ' + source


def _recipe(row: dict, expected_product: str) -> None:
    _need(isinstance(row, dict) and set(row) == RECIPE_KEYS, 'invalid_recipe')
    _need(_name(row['name']) and row['product'] == expected_product
          and _integer(row['product_amount'], 1, 1000000)
          and isinstance(row['ingredients'], dict)
          and 1 <= len(row['ingredients']) <= 32
          and all(_name(name) and _integer(amount, 1, 1000000)
                  for name, amount in row['ingredients'].items()), 'invalid_recipe')


def decode(raw: dict, *, request: dict, expected_epoch: dict,
           expected_route: dict, attachment: dict) -> dict:
    """Bind one native result to the selected route and current actor session."""
    request = validate_request(request)
    _attachment(attachment)
    _need(isinstance(raw, dict) and set(raw) == ROOT_KEYS and raw['schema'] == SCHEMA
          and raw['base_version'] == '2.0.77'
          and raw['request'] == request
          and raw['stock_provenance_qualified'] is False
          and raw['mutation_authorized'] is False, 'invalid_witness')
    if raw['status'] == 'unqualified':
        _need(raw['reason'] in REASONS and raw['recipe_dependency_verified'] is False
              and raw['route'] == {} and raw['producer'] == {} and raw['consumer'] == {},
              'invalid_unqualified_witness')
        return raw
    _need(raw['status'] == 'observed' and raw['reason'] == 'none'
          and raw['recipe_dependency_verified'] is True, 'invalid_observed_witness')
    epoch = raw['epoch']
    _need(isinstance(expected_epoch, dict) and set(expected_epoch) == EPOCH_KEYS - {'tick'}
          | {'min_tick', 'max_tick'} and isinstance(epoch, dict)
          and set(epoch) == EPOCH_KEYS
          and all(epoch[key] == expected_epoch[key] for key in EPOCH_KEYS - {'tick'})
          and epoch['session_id'] == attachment['session_id']
          and epoch['actor_unit'] == attachment['actor_unit']
          and _integer(epoch['tick'], 0)
          and _integer(expected_epoch['min_tick'], 0)
          and _integer(expected_epoch['max_tick'], 0)
          and expected_epoch['min_tick'] <= epoch['tick'] <= expected_epoch['max_tick']
          and expected_epoch['max_tick'] - expected_epoch['min_tick'] <= 120,
          'epoch_mismatch')
    route = raw['route']
    _need(isinstance(expected_route, dict) and set(expected_route) == ROUTE_KEYS
          and isinstance(route, dict) and set(route) == ROUTE_KEYS
          and route == expected_route
          and route['id'] == request['route']
          and _name(route['item']) and _name(route['source_role'])
          and _integer(route['source_unit'])
          and route['target_role'] == request['producer_role']
          and route['target_unit'] == request['producer_unit']
          and _integer(route['paid_parts'], 1, 128), 'route_mismatch')
    producer, consumer = raw['producer'], raw['consumer']
    _need(isinstance(producer, dict) and set(producer) == {'role', 'unit', 'recipe'}
          and producer['role'] == request['producer_role']
          and producer['unit'] == request['producer_unit']
          and isinstance(consumer, dict) and set(consumer) == {'role', 'unit', 'recipe'}
          and consumer['role'] == request['consumer_role']
          and consumer['unit'] == request['consumer_unit'], 'entity_mismatch')
    _recipe(producer['recipe'], request['product_item'])
    _recipe(consumer['recipe'], request['science_pack'])
    _need(request['product_item'] in consumer['recipe']['ingredients']
          and route['item'] in producer['recipe']['ingredients'], 'dependency_mismatch')
    return raw


def decode_response(response: str, **kwargs) -> dict:
    return decode(decode_native(response), **kwargs)


def observe(client, *, request: dict, expected_epoch: dict,
            expected_route: dict, receipt_path=None) -> dict:
    """Perform ordered qualified readback and one read-only native query.

    The owner still holds the existing native single-writer lock and captures
    the checkpoint/window evidence. This helper has no gameplay dispatch.
    """
    attachment = readback(client, receipt_path=receipt_path)
    response = client.send_command(command(request, attachment))
    return decode_response(response, request=request, expected_epoch=expected_epoch,
                           expected_route=expected_route, attachment=attachment)
