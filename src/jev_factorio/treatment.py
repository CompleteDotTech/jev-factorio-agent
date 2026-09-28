"""Immutable opt-in solid/coal treatment supplied before native attachment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .acceptance_io import stable_read
from .backends.solid_routes import validate_intents
from .coal_supply import validate_targets, validate_transport_intents

SCHEMA = 'jev-factorio.production-treatment.v1'
KEYS = {'schema', 'solid_intents', 'coal_targets', 'solid_science_policy', 'coal_kit_policy'}
SCHEMA_V2 = 'jev-factorio.production-treatment.v2'
KEYS_V2 = KEYS | {'coal_economic_admission'}


def validate(value: object) -> dict:
    if (not isinstance(value, dict)
            or (value.get('schema'), set(value)) not in ((SCHEMA, KEYS), (SCHEMA_V2, KEYS_V2))):
        raise ValueError('Invalid production treatment schema')
    flags = ('solid_science_policy', 'coal_kit_policy') + (
        ('coal_economic_admission',) if value['schema'] == SCHEMA_V2 else ())
    if any(type(value[key]) is not bool for key in flags):
        raise ValueError('Production treatment policies must be boolean')
    if value['schema'] == SCHEMA_V2 and value['coal_economic_admission'] is not True:
        raise ValueError('Production treatment v2 requires explicit coal economic admission')
    intents = validate_intents(value['solid_intents'])
    targets = value['coal_targets']
    if targets:
        targets = validate_targets(targets)
        validate_transport_intents(targets, intents)
    elif value['coal_kit_policy']:
        raise ValueError('Coal kit policy requires coal targets')
    elif targets != []:
        raise ValueError('Empty coal targets must be a list')
    if value.get('coal_economic_admission', False) and not value['coal_kit_policy']:
        raise ValueError('Coal economic admission requires the coal kit policy')
    return {**value, 'solid_intents': intents, 'coal_targets': targets}


def digest(value: dict) -> str:
    checked = validate(value)
    canonical = json.dumps(checked, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    return hashlib.sha256(canonical).hexdigest()


def load(path: Path) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError('Production treatment must be a regular file')
    raw = stable_read(path, 65536)
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Duplicate production treatment key')
            result[key] = item
        return result
    value = validate(json.loads(raw, object_pairs_hook=unique))
    return value, digest(value)
