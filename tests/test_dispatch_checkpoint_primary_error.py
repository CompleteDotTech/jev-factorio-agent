"""Real write-ahead/recovery paths with deterministic paid-effect backend doubles."""
from copy import deepcopy
import json

import pytest

from jev_factorio import checkpoint_io
from attempt_helpers import ReceiptBackend, controller as transfer_controller
from test_attempts import prepared_native_transfer_checkpoint
from test_coal_supply_integration import Backend as CoalBackend, controller as coal_controller
from test_solid_route_integration import Backend as SolidBackend, controller as solid_controller


@pytest.mark.parametrize('family', ['coal', 'solid', 'transfer'])
@pytest.mark.parametrize('interrupt', [False, True])
def test_owned_replay_checkpoint_failure_is_primary_and_does_not_repay(family, interrupt, tmp_path, monkeypatch):
    if family == 'transfer':
        backend = ReceiptBackend()
        backend.state.world_kind = 'fle'  # Exercises the retained native-transfer policy, not an engine.
        backend.state.factory['player_bound'] = True
        prepared_native_transfer_checkpoint(tmp_path, backend)
        maker = lambda resume: transfer_controller(tmp_path, backend, resume=resume)
        checkpoint = tmp_path / 'checkpoint.json'
    else:
        backend, factory = ((CoalBackend(), coal_controller) if family == 'coal'
                            else (SolidBackend(), solid_controller))
        maker = lambda resume: factory(backend, tmp_path, resume=resume)
        backend.prepared_once = True
        assert not maker(False).step()['verified']
        checkpoint = backend.checkpoint
    loop = maker(True)
    initial = json.loads(checkpoint.read_text())
    calls_before = len(backend.calls)
    original_execute, original_replace = backend.execute, checkpoint_io.os.replace
    primary = KeyboardInterrupt('fixture checkpoint interruption') if interrupt else OSError('fixture checkpoint failure')
    prepared = None
    armed = False

    def execute(action, parameters):
        nonlocal prepared, armed
        prepared = checkpoint.read_bytes()
        result = original_execute(action, parameters)
        armed = True
        return result

    def replacing(source, destination, *args, **kwargs):
        if armed:
            raise primary
        return original_replace(source, destination, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backend, 'execute', execute)
        patch.setattr(checkpoint_io.os, 'replace', replacing)
        with pytest.raises(BaseException) as caught:
            loop.step()
        assert caught.value is primary
        assert prepared is not None and checkpoint.read_bytes() == prepared
        assert len(backend.calls) == calls_before + 1
        saved = json.loads(prepared)
        assert saved['attempt']['id'] == initial['attempt']['id']
        assert saved['failures'] == initial['failures']
        assert loop.memory.pending == saved['pending']
        native_after = deepcopy(backend.state)
        with pytest.raises(RuntimeError, match='persistence failed'):
            loop.step()
        assert backend.state == native_after and len(backend.calls) == calls_before + 1
    resumed = maker(True)
    assert resumed.step()['verified']
    assert len(backend.calls) == calls_before + 1
    assert resumed.memory.attempt_outcomes[-1]['id'] == saved['attempt']['id']
    assert resumed.memory.pending is None
