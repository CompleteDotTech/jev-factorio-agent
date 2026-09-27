"""Cross-lane ownership/funding/persistence regressions; no engine evidence."""
from copy import deepcopy
import json

import pytest

from jev_factorio import coal_supply as coal
from test_coal_kit_funding import Backend, controller


def advance_past_deadline(backend, state):
    backend.state.tick = state['deadline_tick'] + 1
    for key in ('solid_routes', 'coal_supply'):
        backend.state.factory[key]['tick'] = backend.state.tick


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('invalidator', ['force_index', 'actor_index'])
def test_parent_ownership_fault_cannot_release_coal_funding_or_charge_failure(resume, invalidator, tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    retained = deepcopy(loop.memory.coal_funding)
    assert retained and retained['held'] == retained['kit']
    calls = deepcopy(backend.calls)
    advance_past_deadline(backend, retained)
    # Native parent binding becomes invalid without changing coal geometry.
    backend.state.factory['solid_routes'][invalidator] += 1
    if resume:
        loop = controller(backend, tmp_path, resume=True)
    record = loop.step()
    assert record['status'] == 'uncertain'
    assert backend.calls == calls
    assert loop.memory.coal_funding == retained
    assert loop.memory.failures == {}
    saved = json.loads(backend.checkpoint.read_text())
    assert saved['coal_funding'] == retained
    assert saved['failures'] == {}


@pytest.mark.parametrize('family', ['coal_supply', 'solid_routes'])
@pytest.mark.parametrize('resume_after_fault', [False, True])
def test_sticky_ownership_fault_cannot_later_abandon_funding_when_evidence_recovers(family, resume_after_fault, tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    retained = deepcopy(loop.memory.coal_funding)
    calls = deepcopy(backend.calls)
    backend.state.factory[family]['force_index'] += 1
    assert loop.step()['status'] == 'uncertain'
    assert loop.memory.coal_funding == retained
    backend.state.factory[family]['force_index'] -= 1
    advance_past_deadline(backend, retained)
    if resume_after_fault:
        loop = controller(backend, tmp_path, resume=True)
    assert loop.step()['status'] == 'uncertain'
    assert backend.calls == calls
    assert loop.memory.coal_funding == retained
    assert not loop.memory.failures


@pytest.mark.parametrize('resume', [False, True])
def test_valid_expired_funding_still_releases_and_consumes_stable_project_budget(resume, tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    state = deepcopy(loop.memory.coal_funding)
    advance_past_deadline(backend, state)
    if resume:
        loop = controller(backend, tmp_path, resume=True)
    loop.step()
    assert loop.memory.coal_funding is None
    assert loop.memory.failures[state['key']] == 2
    assert len(backend.calls) == 1


@pytest.mark.parametrize('lost_reply', [False, True])
def test_paid_kit_then_owned_prepared_source_replays_same_attempt_once(lost_reply, tmp_path):
    backend = Backend(); backend.lose_kit_ack = lost_reply
    loop = controller(backend, tmp_path)
    first = loop.step()
    if lost_reply:
        assert not first['verified']
        retained = deepcopy(loop.memory.attempt)
        loop = controller(backend, tmp_path, resume=True)
        assert loop.step()['verified']
        assert loop.memory.attempt_outcomes[-1]['id'] == retained['id']
    else:
        assert first['verified']
    assert len(backend.calls) == 1
    backend.prepared_once = True
    assert not loop.step()['verified']
    attempt = deepcopy(loop.memory.attempt)
    pending = deepcopy(loop.memory.pending)
    calls = deepcopy(backend.calls)
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed.step()['verified']
    assert len(backend.calls) == len(calls) + 1
    assert backend.calls[-1] == calls[-1]
    assert resumed.memory.attempt_outcomes[-1]['id'] == attempt['id']
    assert pending['action'] == coal.COMMAND
    assert sum(len(row['parts']) for row in coal.sources(backend.state).values()) == 1
    assert not coal.flow_complete(backend.state)


@pytest.mark.parametrize('audit', [False, True])
@pytest.mark.parametrize('stage', ['kit', 'source'])
@pytest.mark.parametrize('interrupt', [False, True])
def test_post_payment_checkpoint_failure_preserves_prepared_identity_and_resumes_once(stage, interrupt, audit, tmp_path, monkeypatch):
    from jev_factorio import checkpoint_io
    from test_causal_trace import Sink
    from jev_factorio.research_log import RunConfiguration
    sink = Sink() if audit else None
    if sink is not None:
        sink.configuration = RunConfiguration(
            'mock', 'hierarchical', 'deterministic', factory_scheduling='ready-work',
            solid_routes=True, coal_supply=True, coal_kit_policy=True)
    backend = Backend(); loop = controller(backend, tmp_path, research_log=sink)
    if stage == 'source':
        assert loop.step()['verified']
    calls_before = len(backend.calls)
    armed = False
    prepared = None
    primary = KeyboardInterrupt('fixture persistence interruption') if interrupt else OSError('fixture persistence failure')
    real_replace = checkpoint_io.os.replace

    def arm():
        nonlocal armed, prepared
        prepared = backend.checkpoint.read_bytes()
        assert json.loads(prepared)['pending']['dispatch'] == 'prepared'
        armed = True

    def replacing(source, destination, *args, **kwargs):
        if armed:
            raise primary
        return real_replace(source, destination, *args, **kwargs)

    backend.kit_after = arm
    backend.post_dispatch = arm
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint_io.os, 'replace', replacing)
        with pytest.raises(BaseException) as caught:
            loop.step()
        assert caught.value is primary
        assert len(backend.calls) == calls_before + 1
        assert prepared is not None and backend.checkpoint.read_bytes() == prepared
        saved = json.loads(prepared)
        assert saved['coal_funding'] is not None
        assert saved['pending']['dispatch'] == 'prepared'
        assert loop._persistence_failed
        observations = backend.observations
        with pytest.raises(RuntimeError, match='persistence failed'):
            loop.step()
        assert backend.observations == observations
        assert len(backend.calls) == calls_before + 1
    backend.kit_after = lambda: None
    backend.post_dispatch = lambda: None
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed.step()['verified']
    assert len(backend.calls) == calls_before + 1
    assert resumed.memory.attempt_outcomes[-1]['id'] == saved['attempt']['id']
    assert resumed.memory.failures == saved['failures']
    assert resumed.memory.pending is None
    assert not coal.flow_complete(backend.state)


@pytest.mark.parametrize('resume', [False, True])
def test_unowned_prepared_source_cannot_adopt_acquired_kit_or_replace_pending_pickup(resume, tmp_path):
    backend = Backend(); backend.lose_kit_ack = True
    loop = controller(backend, tmp_path)
    assert not loop.step()['verified']
    retained = deepcopy(loop.memory.coal_funding)
    pending, attempt = deepcopy(loop.memory.pending), deepcopy(loop.memory.attempt)
    calls = deepcopy(backend.calls)
    data = backend.state.factory['coal_supply']
    data['committed'] = True
    for row in data['sources'].values():
        row['state'] = 'building'
    data['sources']['alpha']['pending'] = {'part': 'chest', 'receipt': 'foreign-source-prepare', 'phase': 'prepared'}
    before = deepcopy(backend.state)
    if resume:
        loop = controller(backend, tmp_path, resume=True)
    assert loop.step()['status'] == 'uncertain'
    assert backend.calls == calls and backend.state == before
    assert loop.memory.coal_funding == retained
    assert loop.memory.pending == pending and loop.memory.attempt == attempt
    assert not loop.memory.coal_commitments and not loop.memory.failures
