import copy

import pytest

from jev_factorio.planning.connection_identity import (
    PREFIX, connection_failures, connection_key, validate_attribution,
)


def identity(**changes):
    parameters = dict(source='pump', target='refinery', kind='pipe', fluid='crude-oil')
    parameters.update(changes)
    return PREFIX + connection_key(parameters)


def test_unrelated_connections_have_independent_budgets():
    oil, water = identity(), identity(source='water', fluid='water')
    failures = {oil: 2, water: 1}
    assert connection_failures(oil, failures, {}) == 2
    assert connection_failures(water, failures, {}) == 1
    assert identity() == identity()
    for change in ({'source': 'other'}, {'target': 'other'}, {'kind': 'small-electric-pole'},
                   {'fluid': 'petroleum-gas'}):
        assert identity(**change) != oil


def test_old_exhausted_budget_cannot_be_evaded_by_new_identity():
    assert connection_failures(identity(), {PREFIX: 2}, {}) == 2
    assert connection_failures('capital:x:supply:' + identity(),
                               {'capital:x:supply:' + PREFIX: 2}, {}) == 2


def attributed():
    oil, water = identity(), identity(source='water', fluid='water')
    failures = {PREFIX: 2, oil: 1, water: 1}
    receipt = {PREFIX: {'count': 2, 'allocations': {oil: 1, water: 1},
                        'evidence': 'Private operator audit with exact native attempt fingerprints'}}
    return failures, receipt


def test_explicit_attribution_preserves_legacy_history_and_reblocks_second_failure():
    failures, receipt = attributed()
    before = copy.deepcopy(failures)
    validate_attribution(receipt, failures)
    assert connection_failures(identity(), failures, receipt) == 1
    assert failures == before
    failures[identity()] += 1
    assert connection_failures(identity(), failures, receipt) == 2
    assert failures[PREFIX] == 2


@pytest.mark.parametrize('mutation', ['missing', 'decreased', 'partial', 'extra', 'changed_legacy', 'no_evidence'])
def test_incomplete_or_inconsistent_attribution_fails_closed(mutation):
    failures, receipt = attributed()
    row = receipt[PREFIX]
    if mutation == 'missing':
        failures.pop(identity())
    elif mutation == 'decreased':
        failures[identity()] = 0
    elif mutation == 'partial':
        row['allocations'].pop(identity())
    elif mutation == 'extra':
        row['allocations'][identity(target='other')] = 1
        failures[identity(target='other')] = 1
    elif mutation == 'changed_legacy':
        failures[PREFIX] = 3
    else:
        row['evidence'] = ''
    with pytest.raises(ValueError):
        validate_attribution(receipt, failures)
