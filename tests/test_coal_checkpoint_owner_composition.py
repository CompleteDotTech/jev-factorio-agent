"""Cross-issue fault probes: real controller/filesystem, API-double game only.

These exercise the combination of the coal kit funder, exact native pending
owner checks, and install-bound checkpoints. They do not demonstrate native flow.
"""
from copy import deepcopy
import json
import os
import stat

import pytest

from jev_factorio import checkpoint_io, coal_supply as coal
from jev_factorio.memory import load_checkpoint
from test_coal_kit_funding import Backend, controller


@pytest.mark.parametrize('paid_prefix', [False, True])
@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('phase', ['prepared', 'dispatching', 'placed'])
def test_funded_but_unowned_native_journal_does_not_adopt_or_release_holds(tmp_path, resume, phase, paid_prefix):
    backend = Backend(); loop = controller(backend, tmp_path)
    assert loop.step()['verified']  # One legitimate, paid kit pickup.
    assert loop.memory.coal_funding['held'] == loop.memory.coal_funding['kit']
    if paid_prefix:
        assert loop.step()['verified']  # One paid source chest, still two branches.
    state = deepcopy(loop.memory.coal_funding)
    commitments = deepcopy(loop.memory.coal_commitments)
    loop.memory.failures['retained-other-project'] = 2
    loop._save()
    row = coal.sources(backend.state)['beta']
    row['pending'] = {'part': 'chest', 'receipt': 'unowned-funding-boundary', 'phase': phase}
    calls, native = deepcopy(backend.calls), deepcopy(backend.state)
    if resume:
        loop = controller(backend, tmp_path, resume=True)
    result = loop.step()
    assert result['status'] == 'uncertain', result
    assert backend.calls == calls and backend.state == native
    assert loop.memory.coal_funding == state
    assert loop.memory.failures == {'retained-other-project': 2}
    assert loop.memory.coal_commitments == commitments
    saved = load_checkpoint(backend.checkpoint, backend.state.session_id, 'rocket_launch')
    assert saved.coal_funding == state and saved.pending is None
    row['pending'] = {}  # A disappearing orphan does not acknowledge its owner.
    loop.step()
    assert backend.calls == calls and loop.memory.coal_funding == state


@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('reply', ['prepared', 'placed'])
def test_first_funded_source_recovery_keeps_one_attempt_and_one_payment(tmp_path, resume, reply):
    backend = Backend(); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    funding = deepcopy(loop.memory.coal_funding)
    if reply == 'prepared': backend.prepared_once = True
    else: backend.lost_ack = True
    result = loop.step()
    assert not result['verified'] and loop.memory.pending
    attempt = deepcopy(loop.memory.attempt)
    pending = deepcopy(loop.memory.pending)
    assert pending['action'] == coal.COMMAND
    # An ambiguous response has not yet crossed a fresh observation. Keep the
    # provisional hold until the exact native bundle is reconciled on recovery.
    assert loop.memory.coal_funding == funding
    assert not loop.memory.coal_commitments
    before = dict(backend.state.inventory)
    backend.lost_ack = False
    if resume: loop = controller(backend, tmp_path, resume=True)
    result = loop.step()
    assert result['verified'], result
    assert loop.memory.coal_funding is None
    assert set(loop.memory.coal_commitments) == set(funding['bundle'])
    assert loop.memory.attempt_outcomes[-1]['id'] == attempt['id']
    assert len(backend.calls) == (3 if reply == 'prepared' else 2)
    if reply == 'prepared':
        assert backend.calls[-1] == backend.calls[-2]
        assert backend.state.inventory['wooden-chest'] == before['wooden-chest'] - 1
    else:
        assert backend.state.inventory == before
    assert sum(len(row['parts']) for row in coal.sources(backend.state).values()) == 1
    assert not coal.flow_complete(backend.state)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX install/directory-sync semantics')
@pytest.mark.parametrize('mode', ['output', 'craft'])
@pytest.mark.parametrize('dispatch', ['prepared', 'returned'])
@pytest.mark.parametrize('boundary', ['installed', 'directory_sync'])
def test_funding_checkpoint_conflict_preserves_paid_state_and_stops_all_reuse(
        tmp_path, monkeypatch, mode, dispatch, boundary):
    backend = Backend(mode); loop = controller(backend, tmp_path)
    path = backend.checkpoint
    real_replace, real_sync = os.replace, os.fsync
    fired, retained, captured = False, None, None
    def conflict():
        nonlocal fired, retained, captured
        if fired or not path.exists(): return
        raw = json.loads(path.read_bytes())
        if (raw.get('pending') or {}).get('dispatch') != dispatch: return
        assert raw['coal_funding'] and raw['coal_kit_policy'] is True
        captured = deepcopy(raw)
        fired = True
        raw['reason'] = 'independent installation conflict fixture'
        retained = json.dumps(raw).encode()
        other = path.with_name('conflicting-checkpoint')
        other.write_bytes(retained)
        real_replace(other, path)
    def replace(source, target):
        real_replace(source, target)
        if boundary == 'installed': conflict()
    def sync(fd):
        real_sync(fd)
        if boundary == 'directory_sync' and stat.S_ISDIR(os.fstat(fd).st_mode): conflict()
    monkeypatch.setattr(checkpoint_io.os, 'replace', replace)
    monkeypatch.setattr(checkpoint_io.os, 'fsync', sync)
    with pytest.raises((OSError, RuntimeError)): loop.step()
    assert fired and path.read_bytes() == retained
    assert len(backend.calls) == int(dispatch == 'returned')
    assert loop.memory.coal_funding == captured['coal_funding']
    assert loop.memory.pending == captured['pending']
    assert loop.memory.attempt == captured['attempt']
    calls, native = deepcopy(backend.calls), deepcopy(backend.state)
    # All original histories survive; neither a fresh candidate nor an owner
    # correction can turn this same failed controller into an action authority.
    for _ in range(2):
        with pytest.raises((OSError, RuntimeError)): loop.step()
        assert backend.calls == calls and backend.state == native
        assert path.read_bytes() == retained
    assert loop.memory._checkpoint_cache is None
