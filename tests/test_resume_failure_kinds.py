"""Additional failure-kind regressions for the merged #117 resume boundary."""
from copy import deepcopy

import pytest

from jev_factorio import capital_controller
from test_solid_review_boundaries import resume
from test_solid_route_integration import Backend, controller


@pytest.mark.parametrize('fault', [ValueError, OSError, KeyboardInterrupt])
def test_provisional_pending_memory_is_cleared_before_phase_failure_callback(tmp_path, monkeypatch, fault):
    backend = Backend(); backend.lost_ack = True
    original = controller(backend, tmp_path)
    original.step()
    assert original.memory.pending and original.memory.attempt
    path = backend.checkpoint
    before, calls, inventory = path.read_bytes(), deepcopy(backend.calls), deepcopy(backend.state.inventory)
    resumed = resume(backend, path)
    seen = []
    trace = resumed._diagnostic_trace
    def diagnostic(event):
        if event['status'] == 'failed':
            seen.append(resumed.memory)
        trace(event)
    resumed._diagnostic_trace = diagnostic
    def reject(loop, snapshot):
        assert loop.memory is not None and loop.memory.attempt is not None
        raise fault('fixture rejected after retained memory initialization')
    monkeypatch.setattr(capital_controller, 'observe', reject)
    with pytest.raises(fault):
        resumed._observe()
    # KeyboardInterrupt is not converted into a phase Exception event; either
    # way no diagnostic can persist the provisional loaded state.
    assert all(memory is None for memory in seen)
    if fault is not KeyboardInterrupt:
        assert seen == [None]
    assert resumed.memory is None and path.read_bytes() == before
    assert backend.calls == calls and backend.state.inventory == inventory
    with pytest.raises(RuntimeError, match='reconstruct'):
        resumed.step()
    with pytest.raises(RuntimeError, match='reconstruct'):
        resumed._save()
    assert path.read_bytes() == before
