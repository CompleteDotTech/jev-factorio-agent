"""Coal observers remain inside the #121 complete first-observation barrier."""
from copy import deepcopy
import json
import pytest
from test_coal_supply_integration import Backend, Loop, controller


class ObservedCoalLoop(Loop):
    # The evidence property assignment occurs after coal-specific validation,
    # not in an inner observer. Model an exception or external edit there.
    after_coal = None
    @property
    def _coal_evidence(self):
        return getattr(self, '_evidence', {})
    @_coal_evidence.setter
    def _coal_evidence(self, value):
        self._evidence = value
        if value and self.after_coal:
            self.after_coal(self)


def retained(tmp_path, pending=False):
    backend = Backend()
    loop = controller(backend, tmp_path, kind=ObservedCoalLoop)
    if pending:
        backend.prepared_once = True
        loop.step()
    else:
        loop.memory.active_goal = "rocket_launch"
        loop._observe()
    before = backend.checkpoint.read_bytes()
    other = json.loads(before)
    other['failures']['external-writer'] = 3
    resumed = controller(backend, tmp_path, resume=True, kind=ObservedCoalLoop)
    return resumed, backend, before, json.dumps(other).encode()


@pytest.mark.parametrize('pending', [False, True])
@pytest.mark.parametrize('change', ['replace', 'delete', 'raise', 'interrupt'])
def test_coal_postvalidation_cannot_publish_over_other_bytes_or_after_failure(tmp_path, pending, change):
    loop, backend, before, other = retained(tmp_path, pending)
    calls = deepcopy(backend.calls)
    def failed(self):
        self.memory.failures['provisional-coal'] = 1
        if change == 'replace':
            backend.checkpoint.write_bytes(other)
        if change == 'delete':
            backend.checkpoint.unlink()
        self._save()
        if change == 'interrupt':
            raise KeyboardInterrupt('modeled coal validator interruption')
        if change == 'raise':
            raise RuntimeError('modeled coal validator failure')
    loop.after_coal = failed
    with pytest.raises((ValueError, OSError, RuntimeError, KeyboardInterrupt)):
        loop._observe()
    if change == 'delete':
        assert not backend.checkpoint.exists()
    else:
        assert backend.checkpoint.read_bytes() == (other if change == 'replace' else before)
    assert loop.memory is None and loop._persistence_failed
    assert backend.calls == calls
    backend.checkpoint.write_bytes(before)
    loop.after_coal = None
    with pytest.raises(RuntimeError, match='reconstruct'):
        loop.step()
    assert backend.calls == calls and backend.checkpoint.read_bytes() == before


def test_coal_successful_resume_flushes_once_after_all_validators(tmp_path, monkeypatch):
    loop, backend, before, _ = retained(tmp_path)
    save = loop.memory_type.save
    writes = []
    def recorded(self, target):
        writes.append(deepcopy(self.failures))
        return save(self, target)
    monkeypatch.setattr(loop.memory_type, 'save', recorded)
    def accepted(self):
        self.memory.failures['accepted-coal'] = 1
        self._save()
        assert backend.checkpoint.read_bytes() == before
        assert self._execution_barrier(backend.state)
    loop.after_coal = accepted
    loop._observe()
    assert len(writes) == 1
    assert json.loads(backend.checkpoint.read_bytes())['failures']['accepted-coal'] == 1
    assert not loop._execution_barrier(backend.state)
