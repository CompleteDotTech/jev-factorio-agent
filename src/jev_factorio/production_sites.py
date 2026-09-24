"""Observed joint-site offers and owned-source identity, not proof of material flow."""
from __future__ import annotations

import math
from copy import deepcopy

ROLES = {'recipe:iron-plate', 'recipe:copper-plate', 'growth:iron-plate', 'growth:copper-plate'}
PARTS = {'stone-furnace', 'burner-mining-drill', 'burner-inserter', 'wooden-chest', 'transport-belt'}


def sources(snapshot) -> dict:
    data = snapshot.factory.get('production_sites')
    if data is None:
        return {}  # Legacy/test backends retain explicit manual placement.
    if (not isinstance(data, dict) or type(data.get('protocol')) is not int or data['protocol'] != 1
            or data.get('session_id') != snapshot.session_id or type(data.get('tick')) is not int
            or data['tick'] != snapshot.tick or not isinstance(data.get('sources'), dict)
            or set(data['sources']) - ROLES):
        raise ValueError('Missing or stale production-site evidence')
    for role, row in data['sources'].items():
        if role.startswith('growth:') and 'successors' not in snapshot.factory:
            raise ValueError('Successor production requires its explicit capability')
        if (not isinstance(row, dict) or row.get('state') not in {'proposed', 'owned', 'rejected'}
                or not isinstance(row.get('reason'), str) or not 0 < len(row['reason']) <= 128):
            raise ValueError('Invalid or faulted production-site evidence')
        if row['state'] == 'rejected':
            continue
        position, bill = row.get('position'), row.get('bill')
        if (not isinstance(row.get('anchor'), str) or not row['anchor'].startswith('cell-site:')
                or len(row['anchor']) > 128 or not isinstance(position, dict)
                or set(position) != {'x', 'y'}
                or any(type(v) not in {int, float} or not math.isfinite(v) or v % 1 for v in position.values())
                or type(row.get('belt_count')) is not int or not 1 <= row['belt_count'] <= 64
                or not isinstance(bill, dict) or set(bill) != PARTS
                or bill != {'stone-furnace': 1, 'burner-mining-drill': 1, 'burner-inserter': 2,
                            'wooden-chest': 1, 'transport-belt': row['belt_count']}
                or any(type(v) is not int for v in bill.values())):
            raise ValueError('Invalid joint production-site geometry or bill')
        if row['state'] == 'owned':
            entity = snapshot.factory.get('entities', {}).get(role, {})
            if (type(row.get('source_unit')) is not int or row['source_unit'] <= 0
                    or entity.get('unit_number') != row['source_unit'] or entity.get('name') != 'stone-furnace'
                    or entity.get('position') != position):
                raise ValueError('Production-site owned source changed')
    return data['sources']


def allowed(parameters, snapshot) -> bool:
    try:
        row = sources(snapshot).get(parameters['role'], {})
        return bool(parameters['name'] == 'stone-furnace' and row.get('state') == 'proposed'
                    and row.get('anchor') == parameters['anchor'])
    except (ValueError, KeyError, TypeError):
        return False


def complete(parameters, snapshot) -> bool:
    try:
        row = sources(snapshot).get(parameters['role'], {})
        return bool(parameters['name'] == 'stone-furnace' and row.get('state') == 'owned'
                    and row.get('anchor') == parameters['anchor'])
    except (ValueError, KeyError, TypeError):
        return False


def summary(snapshot) -> dict:
    return {role: {key: deepcopy(row[key]) for key in (
        'state', 'reason', 'anchor', 'position', 'belt_count', 'source_unit', 'bill', 'fallback') if key in row}
        for role, row in sources(snapshot).items()}
