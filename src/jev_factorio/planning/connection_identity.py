"""Stable connection budgets with explicit attribution of legacy failures."""
import hashlib
import json

PREFIX = 'factory:factory_connect:'


def connection_key(parameters):
    identity = {key: parameters[key] for key in ('source', 'target', 'kind', 'fluid')}
    return hashlib.sha256(json.dumps(identity, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def validate_attribution(attribution, failures):
    if not isinstance(attribution, dict):
        raise ValueError('Invalid connection failure attribution')
    for legacy, receipt in attribution.items():
        if (not isinstance(legacy, str) or not legacy.endswith(PREFIX)
                or not isinstance(receipt, dict)
                or set(receipt) != {'count', 'allocations', 'evidence'}):
            raise ValueError('Invalid legacy connection receipt')
        count, allocations = receipt['count'], receipt['allocations']
        if (type(count) is not int or count < 1 or failures.get(legacy) != count
                or not isinstance(allocations, dict) or not allocations
                or not isinstance(receipt['evidence'], str) or not receipt['evidence'].strip()):
            raise ValueError('Unreconciled legacy connection budget')
        for key, value in allocations.items():
            suffix = key[len(legacy):] if isinstance(key, str) and key.startswith(legacy) else ''
            if (len(suffix) != 64 or any(c not in '0123456789abcdef' for c in suffix)
                    or type(value) is not int or value < 1 or failures.get(key, 0) < value):
                raise ValueError('Invalid attributed connection failure')
        if sum(allocations.values()) != count:
            raise ValueError('Legacy connection failures must all be attributed')


def connection_failures(plan_id, failures, attribution):
    """Unattributed legacy counts remain a floor for every derived identity."""
    current = failures.get(plan_id, 0)
    prefix, separator, suffix = plan_id.rpartition(PREFIX)
    if not separator or not suffix:
        return current
    legacy = prefix + separator
    if legacy not in attribution:
        return max(current, failures.get(legacy, 0))
    validate_attribution(attribution, failures)
    return current
