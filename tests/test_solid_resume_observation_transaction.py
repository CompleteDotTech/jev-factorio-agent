"""All composed first-resume observers must finish before checkpoint publication.

These use an in-process backend and real temporary filesystem, never Factorio.
"""
from copy import deepcopy
import json

import pytest

from jev_factorio.solid_controller import solid_loop_type
from test_solid_route_integration import Backend, FoundationScenario, controller


class PostSnapshotObserver(FoundationScenario):
    """Models the existing composed observers' post-snapshot ownership saves."""
    after_snapshot = None

    def _observe(self, stage='observe'):
        snapshot = super()._observe(stage)
        if self.after_snapshot is not None:
            self.after_snapshot(self, snapshot)
        self._save()
        return snapshot


TransactionLoop = solid_loop_type(PostSnapshotObserver)


def retained(tmp_path, *, pending=False):
    backend = Backend()
    origin = controller(backend, tmp_path, kind=TransactionLoop)
    origin.memory.active_goal = 'rocket_launch'
    origin.memory.failures['original-project'] = 2
    if pending:
        backend.prepared_once = True
        origin.step()
        assert origin.memory.pending is not None
    else:
        origin._observe()
    path = backend.checkpoint
    before = path.read_bytes()
    other = json.loads(before)
    other['failures']['another-writer'] = 4
    other_bytes = json.dumps(other).encode()
    loop = controller(backend, tmp_path, resume=True, kind=TransactionLoop)
    return loop, backend, path, before, other_bytes


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('change', ['replacement', 'deletion'])
@pytest.mark.parametrize('then_raise', [False, True])
def test_inner_observer_cannot_overwrite_an_external_change(tmp_path, pending, change, then_raise):
    loop, backend, path, before, other = retained(tmp_path, pending=pending)
    calls = deepcopy(backend.calls)

    def changed(self, snapshot):
        if change == 'replacement':
            path.write_bytes(other)
        else:
            path.unlink()
        # Inner layers currently checkpoint both successful and fault observations.
        self._save()
        if then_raise:
            raise ValueError('fixture post-snapshot validator rejected')

    loop.after_snapshot = changed
    with pytest.raises((ValueError, OSError)):
        loop._observe()
    assert backend.calls == calls
    if change == 'replacement':
        assert path.read_bytes() == other
    else:
        assert not path.exists()
    assert loop.memory is None
    assert loop._persistence_failed and loop._solid_fault
    path.write_bytes(before)
    count = backend.observations
    with pytest.raises(RuntimeError, match='reconstruct'):
        loop.step()
    with pytest.raises(RuntimeError, match='reconstruct'):
        loop._save()
    assert backend.observations == count and path.read_bytes() == before


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('error_type', [ValueError, RuntimeError, KeyboardInterrupt])
def test_inner_failed_observation_never_publishes_provisional_memory(tmp_path, pending, error_type):
    loop, backend, path, before, other = retained(tmp_path, pending=pending)
    calls = deepcopy(backend.calls)

    def rejected(self, snapshot):
        self.memory.failures['unaccepted-observation'] = 1
        self._save()
        raise error_type('fixture post-snapshot failure')

    loop.after_snapshot = rejected
    with pytest.raises(error_type):
        loop._observe()
    assert loop.memory is None
    assert loop._persistence_failed and loop._solid_fault
    assert path.read_bytes() == before and backend.calls == calls


@pytest.mark.parametrize('pending', [False, True])
def test_successful_initial_observation_publishes_once_then_keeps_normal_barriers(tmp_path, monkeypatch, pending):
    loop, backend, path, before, other = retained(tmp_path, pending=pending)
    memory_save = loop.memory_type.save
    writes = []

    def record_save(self, target):
        writes.append(deepcopy(self.failures))
        return memory_save(self, target)

    monkeypatch.setattr(loop.memory_type, 'save', record_save)
    def observed(self, snapshot):
        self.memory.failures['accepted-observation'] = 1
        self._save()
        assert path.read_bytes() == before

    loop.after_snapshot = observed
    loop._observe()
    assert len(writes) == 1
    assert json.loads(path.read_bytes())['failures']['accepted-observation'] == 1
    assert loop.memory is not None and not loop._persistence_failed
    loop.after_snapshot = None
    # Only the initial read-only resume observation can defer saves. Subsequent
    # explicit writes retain the normal synchronous authoritative barrier.
    loop._save()
    assert len(writes) == 2


def test_first_resume_flush_failure_keeps_old_disk_and_prevents_next_action(tmp_path, monkeypatch):
    loop, backend, path, before, other = retained(tmp_path)
    def failed(self, target):
        raise OSError('fixture final observation checkpoint flush failed')
    monkeypatch.setattr(loop.memory_type, 'save', failed)
    with pytest.raises(OSError):
        loop._observe()
    assert path.read_bytes() == before and not backend.calls
    assert loop._persistence_failed
    assert loop._execution_barrier(backend.state)
    from jev_factorio.planning.solid_routes import candidates
    plan = candidates(backend.state, 'rocket_launch')[0]
    assert not loop._step_allowed(plan.steps[0], backend.state)
    with pytest.raises(RuntimeError, match='reconstruct'):
        loop.step()


def test_initial_resume_blocks_action_admission_until_final_flush(tmp_path):
    from jev_factorio.planning.solid_routes import candidates
    loop, backend, path, before, other = retained(tmp_path)
    observed = []
    def admission(self, snapshot):
        plan = candidates(snapshot, 'rocket_launch')[0]
        observed.append(self._execution_barrier(snapshot))
        assert not self._step_allowed(plan.steps[0], snapshot)
        assert path.read_bytes() == before
    loop.after_snapshot = admission
    loop._observe()
    assert observed == [True]
    assert not loop._execution_barrier(backend.state)
    assert not backend.calls


def test_reentrant_resume_observation_poisoned_without_second_native_read(tmp_path):
    loop, backend, path, before, other = retained(tmp_path)
    count = backend.observations
    def recurse(self, snapshot):
        self._observe()
    loop.after_snapshot = recurse
    with pytest.raises(RuntimeError, match='Reentrant'):
        loop._observe()
    assert backend.observations == count + 1
    assert path.read_bytes() == before and loop.memory is None
    assert loop._persistence_failed and not backend.calls


def real_composition(name):
    from jev_factorio.background import BackgroundWorkLoop
    from jev_factorio.buffer_controller import buffered_loop_type
    from jev_factorio.controller import HierarchicalLoop
    from jev_factorio.input_controller import input_loop_type
    from jev_factorio.outpost_controller import outpost_loop_type
    from jev_factorio.successor_controller import successor_loop_type

    base = BackgroundWorkLoop if name in {'background', 'successor'} else HierarchicalLoop
    base = input_loop_type(buffered_loop_type(base))
    if name == 'outpost':
        base = outpost_loop_type(base)
    if name == 'successor':
        base = successor_loop_type(base)
    return solid_loop_type(base)


def composed_retained(tmp_path, name):
    backend = Backend()
    for capability in ('output_buffers', 'input_routes', 'craft_jobs', 'mining_outposts', 'successors'):
        setattr(backend, capability + '_supported', True)
    for extension in ('output_buffers', 'input_routes',
                      *(['mining_outposts'] if name == 'outpost' else []),
                      *(['successors'] if name == 'successor' else [])):
        backend.state.factory[extension] = {
            'protocol': 1, 'session_id': backend.state.session_id,
            'tick': backend.state.tick, 'sources': {},
        }
    kind = real_composition(name)
    origin = controller(backend, tmp_path, kind=kind)
    origin.memory.active_goal = 'rocket_launch'
    origin.memory.failures['retained-project'] = 2
    origin._observe()
    assert origin.memory.status == 'running'
    before = backend.checkpoint.read_bytes()
    return controller(backend, tmp_path, resume=True, kind=kind), backend, before


@pytest.mark.parametrize('composition', ['input', 'background', 'outpost', 'successor'])
def test_real_composed_resume_flushes_once_and_keeps_history(tmp_path, monkeypatch, composition):
    loop, backend, before = composed_retained(tmp_path, composition)
    original_save = loop.memory_type.save
    writes = []

    def saving(self, path):
        writes.append(deepcopy(self.failures))
        return original_save(self, path)

    monkeypatch.setattr(loop.memory_type, 'save', saving)
    loop._observe()
    assert len(writes) == 1
    assert loop.memory.failures == {'retained-project': 2}
    assert loop.memory.status == 'running'
    restored = loop.memory_type.from_bytes(backend.checkpoint.read_bytes(),
                                          backend.state.session_id, 'rocket_launch')
    assert restored.failures == loop.memory.failures
    assert not backend.calls and not loop._execution_barrier(backend.state)


@pytest.mark.parametrize('composition', ['input', 'background', 'outpost', 'successor'])
def test_real_composed_resume_does_not_publish_over_replaced_checkpoint(tmp_path, monkeypatch, composition):
    from jev_factorio.input_controller import InputRouteMixin
    loop, backend, before = composed_retained(tmp_path, composition)
    original_observe = InputRouteMixin._observe
    replacement = json.loads(before)
    replacement['failures']['another-writer'] = 3
    other = json.dumps(replacement).encode()

    def replaced(self, stage='observe'):
        snapshot = original_observe(self, stage)
        backend.checkpoint.write_bytes(other)
        self._save()
        return snapshot

    monkeypatch.setattr(InputRouteMixin, '_observe', replaced)
    with pytest.raises(ValueError, match='Checkpoint changed'):
        loop._observe()
    assert backend.checkpoint.read_bytes() == other
    assert loop.memory is None and loop._persistence_failed
    assert not backend.calls
