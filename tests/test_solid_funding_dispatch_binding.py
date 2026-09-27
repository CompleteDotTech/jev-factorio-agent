"""Funding commits retain selected decisions and uniquely identified dispatches."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_funding_evidence import funded_evidence, event, plan_event, decision_for, assert_rejected
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('change', ['decision_missing', 'decision_plan', 'decision_source',
    'outcome_missing', 'attempt_plan', 'attempt_action', 'attempt_process', 'dispatch_missing',
    'dispatch_started', 'unverified', 'replayed_attempt'])
def test_actual_commit_requires_its_decision_and_new_dispatch(tmp_path, change):
    loop, _ = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    record = loop.step()
    final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    attempt = record['attempt_outcomes'][-1]
    if change == 'decision_missing': record['decision'] = None
    elif change == 'decision_plan': record['decision']['plan_id'] = 'unrelated'
    elif change == 'decision_source': record['decision']['source'] = 'jev'
    elif change == 'outcome_missing': record['attempt_outcomes'] = []
    elif change == 'attempt_plan': attempt['plan_id'] = 'unrelated'
    elif change == 'attempt_action': attempt['action'] = 'factory_extract'
    elif change == 'attempt_process': attempt['process_id'] = '1' * 32
    elif change == 'dispatch_missing': attempt['dispatch_phases'] = {}
    elif change == 'dispatch_started':
        attempt['dispatch_phases']['dispatch'].update(status='started', seconds=None)
    elif change == 'unverified': record['verified'] = False
    else: initial['attempt_outcomes'].append(deepcopy(attempt))
    assert funding_history_issues(initial, [record], final)


def test_project_switch_checks_new_project_paid_growth_as_well_as_old(tmp_path):
    loop, _ = kit_loop(tmp_path)
    prototype = loop.step()
    data = funded_evidence()
    records, _, initial, final = data
    old = deepcopy(initial['solid_funding'])
    paid = next(value for value in records[3]['after_state']['factory']['solid_routes']['routes'].values()
                if value['item'] != 'coal' and value['parts'])
    tick = records[3]['state']['tick']
    new = solid_funding.start(paid, {'catalog_sha256': '2' * 64}, tick)
    initial['solid_commitments'].pop(new['route'])
    for record in records[:3]:
        for label in ('state', 'after_state'):
            record[label]['factory']['solid_routes']['routes'][new['route']].update(state='proposed', parts={}, flow={})
    records[3]['state']['factory']['solid_routes']['routes'][new['route']].update(state='proposed', parts={}, flow={})
    records[3]['state']['factory']['solid_routes']['routes'][old['route']]['layout'] = 'changed-layout'
    history = [event('solid_kit_abandoned', old, tick, reason='kit_endpoint_or_layout_changed'),
               event('solid_kit_committed', new, tick), plan_event(new, tick)]
    record = records[3]
    record.update(action='factory_craft', verified=True, history=deepcopy(history), solid_funding=deepcopy(new))
    decision_for(record, new)
    attempt = deepcopy(prototype['attempt_outcomes'][-1])
    attempt.update(plan_id=new['key'] + ':kit', started_tick=tick, finished_tick=record['after_state']['tick'])
    record['attempt_outcomes'] = [attempt]
    for value in records: value['process_id'] = prototype['process_id']
    history.append(event('solid_kit_paid_handoff', new, records[4]['state']['tick']))
    for record in records[3:]: record['failure_budgets'][old['key'] + ':kit'] = 2
    for record in records[4:]: record.update(history=deepcopy(history), solid_funding=None)
    final.update(solid_funding=None, failures=deepcopy(records[-1]['failure_budgets']))
    assert_rejected(data)
