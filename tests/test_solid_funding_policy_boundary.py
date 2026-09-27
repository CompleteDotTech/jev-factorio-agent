"""Funding replay must preserve policy-off runs and prove every history insertion."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_investment import make_loop
from test_solid_funding_evidence import funded_evidence, assert_rejected


def test_policy_off_paid_dispatch_does_not_require_funding_plan_metadata(tmp_path):
    loop, _ = make_loop(tmp_path, enabled=False)
    loop._observe(); initial = asdict(loop.memory)
    record = loop.step(); final = asdict(loop.memory)
    assert record['verified'] and record['action'] == 'factory_solid_build'
    assert not initial['solid_science_policy'] and record['solid_funding'] is None
    assert not funding_history_issues(initial, [record], final)


@pytest.mark.parametrize('action,outcome', [('factory_craft', 'verified'),
    ('factory_wait', 'wait_expired'), ('factory_connect', 'connection_preflight_rejected')])
def test_nonservice_outcome_cannot_enter_admission_history_without_dispatch(action, outcome):
    data = funded_evidence()
    records, _, initial, final = data
    invented = deepcopy(initial['attempt_outcomes'][0])
    invented.update(id='f' * 32, action=action, plan_id='unobserved-action',
        receipt=None, expected_unit_number=None, outcome=outcome)
    for record in records[3:]: record['attempt_outcomes'].append(deepcopy(invented))
    final['attempt_outcomes'].append(deepcopy(invented))
    assert_rejected(data)


@pytest.mark.parametrize('change', ['erase', 'append', 'reorder', 'rewrite', 'record_erase', 'record_rewrite'])
def test_retained_outcome_history_cannot_be_replaced_at_a_boundary(change):
    data = funded_evidence()
    records, _, _, final = data
    if change == 'erase': final['attempt_outcomes'] = []
    elif change == 'append':
        value = deepcopy(final['attempt_outcomes'][-1]); value['id'] = 'f' * 32
        final['attempt_outcomes'].append(value)
    elif change == 'reorder': final['attempt_outcomes'].reverse()
    elif change == 'rewrite': final['attempt_outcomes'][-1]['expected_unit_number'] = 999999
    elif change == 'record_erase': records[3]['attempt_outcomes'] = []
    else: records[3]['attempt_outcomes'][-1]['expected_unit_number'] = 999999
    assert_rejected(data)
