"""Funding proofs must describe the actual one-step controller order."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.planning import solid_funding
from jev_factorio.solid_funding_evidence import funding_history_issues
from integration_evidence_fixtures import evidence
from solid_routes_fixtures import row
from test_solid_funding_evidence import funded_evidence, event, assert_rejected
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('increment', [False, True])
def test_retained_funding_requires_unexhausted_current_budget(increment):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    key = proof['key'] + ':kit'
    initial['failures'][key] = 1 if increment else 2
    for record in records:
        record['failure_budgets'][key] = initial['failures'][key]
    if increment:
        proof['actions'] = 2
        records[3]['history'] = [event('solid_kit_committed', proof, records[3]['state']['tick'])]
    for record in records[3 if increment else 0:]:
        record['solid_funding'] = deepcopy(proof)
        record['failure_budgets'][key] = 2
    final['solid_funding'] = deepcopy(proof)
    final['failures'] = deepcopy(records[-1]['failure_budgets'])
    assert_rejected(data)


@pytest.mark.parametrize('starting', [False, True])
@pytest.mark.parametrize('offset', [1, 3600])
def test_commit_tick_must_be_the_planning_observation(starting, offset):
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    tick = records[3]['state']['tick'] + offset
    if starting:
        initial['solid_funding'] = None
        for record in records[:3]:
            record['solid_funding'] = None
        proof.update(started_tick=tick, deadline_tick=tick + solid_funding.MAX_TICKS)
    else:
        proof['actions'] = 2
    records[3]['history'] = [event('solid_kit_committed', proof, tick)]
    for record in records[3:]:
        record['solid_funding'] = deepcopy(proof)
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


def test_commit_cannot_use_a_proposal_first_seen_after_the_action():
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    tick = records[3]['state']['tick']
    proof.update(started_tick=tick, deadline_tick=tick + solid_funding.MAX_TICKS)
    initial['solid_funding'] = None
    for record in records[:3]:
        record['solid_funding'] = None
        for label in ('state', 'after_state'):
            record[label]['factory']['solid_routes']['routes'].pop(proof['route'])
    records[3]['state']['factory']['solid_routes']['routes'].pop(proof['route'])
    records[3]['history'] = [event('solid_kit_committed', proof, tick)]
    for record in records[3:]:
        record['solid_funding'] = deepcopy(proof)
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


def paid_funding_evidence():
    data = evidence()
    records, trial, initial, final = data
    trial['configuration']['solid_science_policy'] = True
    route = next(v for v in records[0]['state']['factory']['solid_routes']['routes'].values()
                 if v['item'] != 'coal')
    proof = solid_funding.start(route, {'catalog_sha256': '1' * 64}, initial['last_tick'])
    for checkpoint in (initial, final):
        checkpoint['solid_science_policy'] = True
        checkpoint['solid_funding'] = deepcopy(proof)
    for record in records:
        record['acceptance_configuration']['solid_science_policy'] = True
        record.update(solid_funding_schema=1, solid_funding=deepcopy(proof), history=[])
    return data


@pytest.mark.parametrize('release_at', [None, 3])
def test_paid_funding_cannot_outlive_the_first_eligible_observation(release_at):
    data = paid_funding_evidence()
    records, _, initial, final = data
    if release_at is not None:
        records[release_at]['history'] = [event('solid_kit_paid_handoff', initial['solid_funding'],
                                               records[release_at]['state']['tick'])]
        for record in records[release_at:]:
            record['solid_funding'] = None
        final['solid_funding'] = None
    assert_rejected(data)


def test_one_step_cannot_commit_acquisition_then_handoff_new_payment():
    data = paid_funding_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    initial['solid_funding'] = final['solid_funding'] = None
    initial['solid_commitments'].pop(proof['route'])
    proposal = records[0]['state']['factory']['solid_routes']['routes'][proof['route']]
    proposal.update(state='proposed', parts={}, flow={})
    for record in records:
        record['solid_funding'] = None
    records[0]['history'] = [event('solid_kit_committed', proof, records[0]['state']['tick']),
                             event('solid_kit_paid_handoff', proof, records[0]['after_state']['tick'])]
    assert funding_history_issues(initial, records, final)
    assert_rejected(data)


@pytest.mark.parametrize('response', ['returned', 'lost_ack', 'prepared'])
def test_actual_controller_releases_after_the_pending_payment_boundary(tmp_path, response):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    records = []
    backend.lost_ack = response == 'lost_ack'
    backend.prepared_once = response == 'prepared'
    for _ in range(20):
        records.append(loop.step())
        assert not funding_history_issues(initial, records, asdict(loop.memory))
        if row(backend.state)['parts']:
            break
    else:
        pytest.fail('Fixture did not pay its first corridor component')
    assert loop.memory.solid_funding is not None
    if response == 'lost_ack':
        assert records[-1]['pending'] is not None
        records.append(loop.step())
        assert records[-1]['action'] == 'verify' and records[-1]['pending'] is None
        assert loop.memory.solid_funding is not None
        assert not funding_history_issues(initial, records, asdict(loop.memory))
    backend.lost_ack = False
    records.append(loop.step())
    assert loop.memory.solid_funding is None
    assert not funding_history_issues(initial, records, asdict(loop.memory))


def test_new_commit_cannot_choose_a_different_funding_horizon():
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    tick = records[3]['state']['tick']
    proof.update(started_tick=tick, deadline_tick=tick + solid_funding.MAX_TICKS - 1)
    initial['solid_funding'] = None
    for record in records[:3]:
        record['solid_funding'] = None
    records[3]['history'] = [event('solid_kit_committed', proof, tick)]
    for record in records[3:]:
        record['solid_funding'] = deepcopy(proof)
    final['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


def test_expired_funding_cannot_remain_held_through_eligible_observations():
    data = funded_evidence()
    records, _, initial, final = data
    proof = deepcopy(initial['solid_funding'])
    proof['deadline_tick'] = proof['started_tick'] + 1
    initial['solid_funding'] = final['solid_funding'] = deepcopy(proof)
    for record in records:
        record['solid_funding'] = deepcopy(proof)
    assert_rejected(data)


def test_actual_pending_acquisition_defers_expiry_until_reconciliation(tmp_path):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    backend.lose_kit_ack = True
    def advance_past_deadline():
        backend.state.tick += solid_funding.MAX_TICKS + 1
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
    backend.kit_after = advance_past_deadline
    records = [loop.step()]
    assert records[-1]['pending'] is not None
    assert not funding_history_issues(initial, records, asdict(loop.memory))
    records.append(loop.step())
    assert records[-1]['action'] == 'verify' and records[-1]['pending'] is None
    assert loop.memory.solid_funding is not None
    assert not funding_history_issues(initial, records, asdict(loop.memory))
    records.append(loop.step())
    assert loop.memory.solid_funding is None
    assert not funding_history_issues(initial, records, asdict(loop.memory))


def test_pending_verification_record_cannot_also_commit_another_kit_plan():
    records, _, initial, final = funded_evidence()
    proof = deepcopy(initial['solid_funding'])
    proof['actions'] = 2
    records[2]['pending'] = {'action': 'factory_craft', 'dispatch': 'returned',
                             'started_tick': records[2]['state']['tick'], 'polls': 0}
    records[3]['history'] = [event('solid_kit_committed', proof, records[3]['state']['tick'])]
    for record in records[3:]:
        record['solid_funding'] = deepcopy(proof)
    final['solid_funding'] = deepcopy(proof)
    assert funding_history_issues(initial, records, final)


@pytest.mark.parametrize('location', ['initial', 'first_record', 'later_record'])
def test_pending_state_must_be_explicit_for_reconciliation_boundaries(location):
    data = funded_evidence()
    records, _, initial, _ = data
    value = initial if location == 'initial' else records[0 if location == 'first_record' else 3]
    value.pop('pending')
    assert_rejected(data)
