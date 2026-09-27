"""Keep changing coal quantities from bypassing existing resource-site failures."""
from __future__ import annotations

import json
import math
import re

PREFIX = 'factory:factory_gather:coal:target:'
REMAINDER = re.compile(r':remainder-quantity:[1-9][0-9]*$')


def _site(key: str):
    if not isinstance(key, str) or len(key) > 4096:
        return None
    _, found, tail = key.rpartition(PREFIX)
    if not found:
        return None
    amount, marker, encoded = tail.partition(':site:')
    if not marker or not amount.isdigit():
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(encoded)
    except (ValueError, TypeError):
        return None
    if encoded[end:] and not REMAINDER.fullmatch(encoded[end:]):
        return None
    if (not isinstance(value, dict) or set(value) != {'name', 'surface_index', 'position'}
            or value['name'] != 'coal' or type(value['surface_index']) is not int
            or value['surface_index'] <= 0 or not isinstance(value['position'], dict)
            or set(value['position']) != {'x', 'y'}
            or any(type(v) not in {int, float} or not math.isfinite(v)
                   for v in value['position'].values())):
        return None
    return value


def acquisition_failures(plan, failures: dict) -> int:
    """Aggregate old counts without renaming plans or changing persisted history.

    The controller's separately receipt-proven gather-remainder path retains its
    existing semantics. Ordinary new service quantities cannot invoke that path.
    This is a conservative site floor for the new grouped-service acquisitions,
    not a migration or a reset of any existing budget.
    """
    current = failures.get(plan.id, 0)
    if (not (plan.materials or {}).get('fuel_service') or len(plan.steps) != 1
            or plan.steps[0].action != 'factory_gather'
            or plan.steps[0].parameters.get('resource') != 'coal'
            or REMAINDER.search(plan.id)):
        return current
    site = _site(plan.id)
    if site is None:
        return current
    count = sum(value for key, value in failures.items()
                if type(value) is int and value > 0 and _site(key) == site)
    # Legacy unsited failures cannot be silently attributed to another site.
    legacy = max((value for key, value in failures.items()
                  if isinstance(key, str) and key.endswith('factory:factory_gather:coal')
                  and type(value) is int and value > 0), default=0)
    return max(current, count, legacy)
