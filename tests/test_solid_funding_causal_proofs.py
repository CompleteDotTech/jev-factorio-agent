"""Replayed events and newly written budgets do not prove their own cause."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_funding_evidence import funded_evidence, event, plan_event, close_funding, assert_rejected, decision_for
from test_solid_funding_transition_boundaries import paid_funding_evidence
from test_solid_kit_acquisition import kit_loop
from solid_routes_fixtures import row


@pytest.mark.parametrize('change', ['preloaded', 'extra_field', 'missing_source', 'unknown_source',
                                   'before_funding', 'repeated_only', 'duplicate'])
def test_kit_plan_proof_requires_current_exact_event(change):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    proof['actions'] = 2
    tick = records[3]['state']['tick']
    plan = plan_event(proof, tick)
    if change == 'preloaded':
        initial['history'].append(deepcopy(plan))
    elif change == 'extra_field':
        plan['invented'] = True
    elif change == 'missing_source':
        plan.pop('source')
    elif change == 'unknown_source':
        plan['source'] = 'invented'
    records[3]['history'] = [event('solid_kit_committed', proof, tick), plan]
    if change in {'before_funding', 'repeated_only'}:
        records[3]['history'].reverse()
    if change == 'repeated_only':
        records[2]['history'] = [deepcopy(plan)]
    if change == 'duplicate':
        records[3]['history'].append(deepcopy(plan))
    for record in records[3:]:
        record['solid_funding'] = deepcopy(proof)
        decision_for(record, proof)
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


@pytest.mark.parametrize('reason', ['kit_deadline', 'invented', 'kit_action_budget',
                                    'kit_failure_budget', 'kit_endpoint_or_layout_changed',
                                    'kit_catalog_changed', 'kit_demand_or_payback_changed',
                                    'kit_evidence_unavailable'])
def test_current_exhausted_budget_alone_cannot_prove_abandonment_trigger(reason):
    data = funded_evidence()
    records, _, initial, _ = data
    original_deadline = initial['solid_funding']['deadline_tick']
    close_funding(data)
    initial['solid_funding']['deadline_tick'] = original_deadline
    for record in records[:3]:
        record['solid_funding']['deadline_tick'] = original_deadline
    release = records[3]['history'][0]
    release['funding']['deadline_tick'] = original_deadline
    release['reason'] = reason
    assert_rejected(data)


@pytest.mark.parametrize('boundary', ['between_records', 'within_record'])
def test_paid_route_growth_needs_matching_build_evidence(boundary):
    data = paid_funding_evidence()
    records, _, initial, final = data
    proof = initial['solid_funding']
    route = proof['route']
    initial['solid_commitments'].pop(route)
    for record in records[:3]:
        for label in ('state', 'after_state'):
            record[label]['factory']['solid_routes']['routes'][route].update(state='proposed', parts={}, flow={})
    if boundary == 'within_record':
        records[3]['state']['factory']['solid_routes']['routes'][route].update(state='proposed', parts={}, flow={})
    release_at = 3 if boundary == 'between_records' else 4
    records[release_at]['history'] = [event('solid_kit_paid_handoff', proof, records[release_at]['state']['tick'])]
    for record in records[release_at:]:
        record['solid_funding'] = None
    final['solid_funding'] = None
    assert_rejected(data)


def test_identical_plan_event_values_can_describe_new_occurrences_at_same_tick():
    records, _, initial, final = funded_evidence()
    # Controller histories retain occurrences, including identical plan events
    # when game time has not advanced. Funding action proofs distinguish them.
    records = [deepcopy(records[0]) for _ in range(2)]
    tick = initial['last_tick']
    previous = event('solid_kit_committed', initial['solid_funding'], tick)
    plan = plan_event(initial['solid_funding'], tick)
    initial['history'] = [previous, deepcopy(plan)]
    proof = deepcopy(initial['solid_funding'])
    history = deepcopy(initial['history'])
    for record in records:
        proof['actions'] += 1
        history.extend([event('solid_kit_committed', proof, tick), deepcopy(plan)])
        record['history'] = deepcopy(history[-8:])
        record['solid_funding'] = deepcopy(proof)
        record.update(action='verify', verified=True)
        record['after_state']['inventory']['iron-gear-wheel'] = 1
        decision_for(record, proof)
        previous = record['history'][-2]
    final['solid_funding'] = deepcopy(proof)
    assert not funding_history_issues(initial, records, final)


@pytest.mark.parametrize('change', ['missing', 'receipt', 'plan', 'action'])
def test_actual_paid_component_needs_the_matching_recorded_attempt(tmp_path, change):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    records = []
    for _ in range(20):
        records.append(loop.step())
        if row(backend.state)['parts']:
            break
    assert row(backend.state)['parts']
    assert not funding_history_issues(initial, records, asdict(loop.memory))
    if change == 'missing':
        records[-1]['attempt_outcomes'] = []
    elif change == 'receipt':
        records[-1]['attempt_outcomes'][-1]['receipt'] = 'unrelated-receipt'
    elif change == 'plan':
        records[-1]['attempt_outcomes'][-1]['plan_id'] = 'unrelated-plan'
    else:
        records[-1]['action'] = 'observe'
    assert funding_history_issues(initial, records, asdict(loop.memory))
