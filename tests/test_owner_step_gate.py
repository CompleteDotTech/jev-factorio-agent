"""The optional owner gate never silently admits a second native step."""
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_factorio.loop import AgentLoop
from jev_factorio.owner_step_gate import OwnerStepGate, StepGateClosed, _durable_exclusive
from jev_factorio.planning.connection_identity import connection_key


@pytest.fixture
def gate_fixture(tmp_path, monkeypatch):
    if os.name != 'posix':
        pytest.skip('Owner gate requires inherited POSIX lock')
    source = tmp_path / 'source'
    source.mkdir()
    checkpoint = tmp_path / 'checkpoint.json'
    checkpoint.write_text(json.dumps({'tick': 1, 'outcomes': [
        {'id': 'prior', 'outcome': 'verified'}]}))
    checkpoint.chmod(0o600)
    receipt = tmp_path / 'receipt.json'
    receipt.write_text('{}')
    receipt.chmod(0o600)
    lock_path = tmp_path / 'single-writer.lock'
    lock_path.touch(mode=0o600)
    lock = lock_path.open('r+b')
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    monkeypatch.setattr(OwnerStepGate, '_git', lambda self, *args: (
        'b' * 40 if args[0] == 'show' else
        'a' * 40 if args[0] == 'rev-parse' else ''))

    memory = SimpleNamespace(session_id='session', status='running', pending=None,
                             attempt=None, background_job=None, background_attempt=None,
                             transfer_recovery=None, reservations={}, last_tick=1,
                             attempt_outcomes=[{'id': 'prior', 'outcome': 'verified'}])
    memory.active_plan = None
    memory.connector_ownership = None
    memory.capital_investment = None
    memory.solid_funding = None
    memory.coal_funding = None
    for name in ('solid_commitments', 'coal_commitments', 'output_commitments',
                 'input_commitments', 'outpost_commitments', 'successor_projects'):
        setattr(memory, name, {})

    class MemoryType:
        @staticmethod
        def from_bytes(raw, session_id, target):
            assert session_id == 'session' and target == 'rocket_launch'
            saved = SimpleNamespace(**vars(memory))
            decoded = json.loads(raw)
            saved.last_tick = decoded['tick']
            saved.attempt_outcomes = decoded['outcomes']
            for name, owner in decoded.get('owners', {}).items():
                setattr(saved, name, owner)
            return saved

    snapshot = SimpleNamespace(session_id='session', _native_controls={
        'walking': False, 'mining': False, 'status': 'completed'},
        factory={'crafting_queue': 0, 'player_bound': True,
                 'receipts': {'prior': {'quantity': 1}}})
    backend = SimpleNamespace(observe=lambda: snapshot)
    loop = SimpleNamespace(memory=memory, memory_type=MemoryType,
                           target='rocket_launch', backend=backend, policy='jev',
                           jev=SimpleNamespace(state={'phase': 'healthy', 'in_flight': None}))

    def make_gate(*, sleep, wait_seconds=1, clock=None, native_readback=None):
        return OwnerStepGate(tmp_path / 'gate', checkpoint, receipt, source,
                             lock_path, lock.fileno(), wait_seconds=wait_seconds,
                             sleep=sleep, **({'clock': clock} if clock else {}),
                             native_readback=native_readback or (lambda _: {
                                 'qualified': True, 'session_id': 'session',
                                 'native_installation': {'profile': 'p'}}))
    yield make_gate, loop, checkpoint
    lock.close()


def _grant(request_path: Path, *, changes=None):
    request = json.loads(request_path.read_text())
    grant = {key: value for key, value in request.items()
             if key not in {'phase', 'verified', 'actor_idle', 'automatic_continuation'}}
    grant['decision'] = 'continue'
    grant.update(changes or {})
    _durable_exclusive(request_path.with_name('step-0001-grant.json'), grant)


def _empty_connector_binding():
    return {'protocol': 1, 'session_id': 'session', 'routes': {}}


def _paid_connector_binding():
    route = {'source': 'utility:boiler', 'target': 'utility:engine',
             'kind': 'pipe', 'fluid': 'steam'}
    receipt = connection_key(route)
    return {'protocol': 1, 'session_id': 'session', 'routes': {receipt: {
        'id': receipt, **route, 'source_unit': 11, 'target_unit': 12,
        'actor_unit': 13, 'surface_index': 1, 'force_index': 1,
        'session_id': 'session', 'state': 'complete', 'paid': 1,
        'external': 0, 'owned': True, 'pending': None,
        'cells': [{'index': 1, 'position': {'x': .5, 'y': 1.5},
                   'unit_number': 100, 'paid': True, 'external': False}],
    }}}


def test_matching_grant_has_durable_acceptance_before_next_step(gate_fixture, monkeypatch):
    make_gate, loop, _ = gate_fixture
    monkeypatch.setenv('TYPESAFE_API_KEY', 'private-marker-must-not-appear')
    waited = []

    def sleep(_):
        waited.append(True)
        _grant(make_gate_instance.directory / 'step-0001-request.json')

    make_gate_instance = make_gate(sleep=sleep)
    assert make_gate_instance(loop, 1, {'verified': True, 'status': 'running'}) is True
    assert waited
    accepted = json.loads((make_gate_instance.directory / 'step-0001-accepted.json').read_text())
    assert accepted['phase'] == 'accepted_before_next_step'
    assert accepted['gate_wall_ns'] >= 0 and accepted['gate_process_cpu_ns'] >= 0
    assert accepted['checkpoint_sha256'] == json.loads(
        (make_gate_instance.directory / 'step-0001-request.json').read_text())['checkpoint_sha256']
    assert all('private-marker-must-not-appear' not in path.read_text()
               for path in make_gate_instance.directory.iterdir())


def test_empty_session_bound_connector_checkpoint_allows_grant(gate_fixture):
    make_gate, loop, checkpoint = gate_fixture
    binding = _empty_connector_binding()
    loop.memory.connector_ownership = binding
    checkpoint.write_text(json.dumps({'tick': 1, 'outcomes': [
        {'id': 'prior', 'outcome': 'verified'}],
        'owners': {'connector_ownership': binding}}))

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')

    gate = make_gate(sleep=sleep)
    assert gate(loop, 1, {'verified': True, 'status': 'running'}) is True
    assert (gate.directory / 'step-0001-accepted.json').exists()


@pytest.mark.parametrize('binding', [
    {'routes': {}},
    {'protocol': True, 'session_id': 'session', 'routes': {}},
    {'protocol': 1, 'session_id': 'other', 'routes': {}},
    {'protocol': 1, 'session_id': 'session', 'routes': []},
    {'protocol': 1, 'session_id': 'session', 'routes': {}, 'active': None},
    {'protocol': 1, 'session_id': 'session', 'routes': {'route': {}}},
    _paid_connector_binding(),
])
def test_invalid_or_nonempty_connector_checkpoint_blocks_request(gate_fixture, binding):
    make_gate, loop, checkpoint = gate_fixture
    loop.memory.connector_ownership = binding
    checkpoint.write_text(json.dumps({'tick': 1, 'outcomes': [
        {'id': 'prior', 'outcome': 'verified'}],
        'owners': {'connector_ownership': binding}}))
    gate = make_gate(sleep=lambda _: None)
    with pytest.raises(StepGateClosed, match='unresolved ownership'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not list(gate.directory.iterdir())


def test_durable_and_live_empty_connector_bindings_must_agree(gate_fixture):
    make_gate, loop, checkpoint = gate_fixture
    loop.memory.connector_ownership = _empty_connector_binding()
    checkpoint.write_text(json.dumps({'tick': 1, 'outcomes': [
        {'id': 'prior', 'outcome': 'verified'}],
        'owners': {'connector_ownership': None}}))
    gate = make_gate(sleep=lambda _: None)
    with pytest.raises(StepGateClosed, match='ownership differ'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not list(gate.directory.iterdir())


@pytest.mark.parametrize('change', [
    {'checkpoint_sha256': '0' * 64}, {'run_nonce': 'stale'},
    {'owner_start_ticks': 0}, {'decision': 'deny'}])
def test_wrong_or_stale_grant_cannot_admit_another_step(gate_fixture, change):
    make_gate, loop, _ = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json', changes=change)

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='does not bind'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_changed_checkpoint_after_grant_stops_before_acceptance(gate_fixture):
    make_gate, loop, checkpoint = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')
        checkpoint.write_text(json.dumps({'tick': 2, 'outcomes': [
            {'id': 'prior', 'outcome': 'verified'}]}))

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='differ'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_changed_native_paid_receipt_after_grant_stops(gate_fixture):
    make_gate, loop, _ = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')
        loop.backend.observe().factory['receipts']['prior']['quantity'] = 2

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='lost source, checkpoint or native'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_provider_becoming_unhealthy_after_grant_stops(gate_fixture):
    make_gate, loop, _ = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')
        loop.jev.state['phase'] = 'open'

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='Provider has unresolved work'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_durable_attempt_outcome_tamper_after_grant_stops(gate_fixture):
    make_gate, loop, checkpoint = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')
        checkpoint.write_text(json.dumps({'tick': 1, 'outcomes': [
            {'id': 'prior', 'outcome': 'reversed'}]}))

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='lost source, checkpoint or native'):
        gate(loop, 1, {'verified': True, 'status': 'running',
                       'attempt_outcomes': loop.memory.attempt_outcomes})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_source_tree_change_after_grant_stops(gate_fixture):
    make_gate, loop, _ = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')
        gate._git = lambda *args: 'c' * 40 if args[0] == 'show' else (
            'a' * 40 if args[0] == 'rev-parse' else '')

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='lost source, checkpoint or native'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_replaced_lock_during_final_native_readback_stops(gate_fixture, tmp_path):
    make_gate, loop, _ = gate_fixture
    calls = []

    def native(_):
        calls.append(True)
        if len(calls) == 2:
            lock_path = tmp_path / 'single-writer.lock'
            lock_path.unlink()
            lock_path.touch(mode=0o600)
        return {'qualified': True, 'session_id': 'session',
                'native_installation': {'profile': 'p'}}

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')

    gate = make_gate(sleep=sleep, native_readback=native)
    with pytest.raises(StepGateClosed, match='lock identity changed'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


@pytest.mark.parametrize('record', [
    {'verified': False, 'status': 'running'},
    {'verified': True, 'status': 'blocked'}])
def test_nonverified_or_terminal_step_never_requests_grant(gate_fixture, record):
    make_gate, loop, _ = gate_fixture
    gate = make_gate(sleep=lambda _: None)
    with pytest.raises(StepGateClosed, match='not verified'):
        gate(loop, 1, record)
    assert not list(gate.directory.iterdir())


def test_loop_gate_denial_prevents_second_step(monkeypatch):
    monkeypatch.setattr('jev_factorio.loop.time.sleep', lambda _: None)
    calls = []
    loop = SimpleNamespace(tick_seconds=0, terminal=False,
                           step=lambda: calls.append('step') or {
                               'verified': True, 'status': 'running'})
    AgentLoop.run(loop, steps=3, after_step=lambda *args: False)
    assert calls == ['step']


def test_loop_gate_exception_does_not_enter_transient_http_retry(monkeypatch):
    calls = []
    loop = SimpleNamespace(tick_seconds=0, terminal=False,
                           step=lambda: calls.append('step') or {
                               'verified': True, 'status': 'running'})
    with pytest.raises(StepGateClosed):
        AgentLoop.run(loop, steps=3,
                      after_step=lambda *args: (_ for _ in ()).throw(StepGateClosed('closed')))
    assert calls == ['step']


def test_existing_gate_directory_forbids_crash_reuse(gate_fixture):
    make_gate, _, _ = gate_fixture
    make_gate(sleep=lambda _: None)
    with pytest.raises(StepGateClosed, match='already used'):
        make_gate(sleep=lambda _: None)


def test_pending_owner_cannot_issue_request(gate_fixture):
    make_gate, loop, _ = gate_fixture
    gate = make_gate(sleep=lambda _: None)
    loop.memory.pending = {'dispatch': 'returned'}
    with pytest.raises(StepGateClosed, match='unresolved ownership'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not list(gate.directory.iterdir())


@pytest.mark.parametrize('name,owner', [
    ('active_plan', {'id': 'active'}),
    ('output_commitments', {'route': 'held'}),
    ('input_commitments', {'route': 'held'}),
    ('outpost_commitments', {'site': 'held'}),
    ('successor_projects', {'project': 'active'}),
    ('connector_ownership', {'routes': {}}),
    ('capital_investment', {'held': 1}),
    ('solid_funding', {'held': 1}),
    ('coal_funding', {'held': 1}),
])
def test_composed_owner_blocks_request(gate_fixture, name, owner):
    make_gate, loop, _ = gate_fixture
    setattr(loop.memory, name, owner)
    gate = make_gate(sleep=lambda _: None)
    with pytest.raises(StepGateClosed, match='unresolved ownership'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not list(gate.directory.iterdir())


@pytest.mark.parametrize('name,owner', [
    ('active_plan', {'id': 'active'}),
    ('output_commitments', {'route': 'held'}),
    ('input_commitments', {'route': 'held'}),
    ('outpost_commitments', {'site': 'held'}),
    ('successor_projects', {'project': 'active'}),
    ('connector_ownership', {'routes': {}}),
    ('capital_investment', {'held': 1}),
    ('solid_funding', {'held': 1}),
    ('coal_funding', {'held': 1}),
])
def test_composed_owner_appearing_after_grant_blocks_acceptance(gate_fixture, name, owner):
    make_gate, loop, _ = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')
        setattr(loop.memory, name, owner)

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='unresolved ownership'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_durable_composed_owner_appearing_after_grant_blocks_acceptance(gate_fixture):
    make_gate, loop, checkpoint = gate_fixture

    def sleep(_):
        _grant(gate.directory / 'step-0001-request.json')
        checkpoint.write_text(json.dumps({'tick': 1, 'outcomes': [
            {'id': 'prior', 'outcome': 'verified'}],
            'owners': {'input_commitments': {'route': 'held'}}}))

    gate = make_gate(sleep=sleep)
    with pytest.raises(StepGateClosed, match='unresolved ownership'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    assert not (gate.directory / 'step-0001-accepted.json').exists()


def test_missing_grant_durably_closes_without_next_step(gate_fixture):
    make_gate, loop, _ = gate_fixture
    counter = iter((0.0, 1.0))
    gate = make_gate(sleep=lambda _: None, clock=lambda: next(counter))
    with pytest.raises(StepGateClosed, match='timed out'):
        gate(loop, 1, {'verified': True, 'status': 'running'})
    closed = json.loads((gate.directory / 'step-0001-closed.json').read_text())
    assert closed['phase'] == 'grant_timeout'
    assert closed['automatic_continuation'] is False
    assert not (gate.directory / 'step-0001-accepted.json').exists()
