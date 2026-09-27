"""Funding release waits for the complete composed ownership observation."""
from copy import deepcopy

import pytest

from test_coal_kit_funding import Backend, controller
from test_solid_funding_coal_reservations import composed_kit_loop


@pytest.mark.parametrize('fault', ['none', 'uncertain', 'outer_coal', 'inner_solid'])
def test_composed_downstream_expiry_waits_for_all_ownership_checks(tmp_path, fault):
    loop, backend = composed_kit_loop(tmp_path)
    assert loop.step()['verified']
    state = loop.memory.solid_funding
    assert state is not None
    state['deadline_tick'] = state['started_tick'] + 1
    backend.state.tick = max(backend.state.tick, state['deadline_tick'])
    backend.state.factory['coal_supply']['tick'] = backend.state.tick
    backend.state.factory['solid_routes']['tick'] = backend.state.tick
    loop.memory.failures['unrelated'] = 1
    if fault == 'uncertain':
        loop.memory.status = 'uncertain'
    elif fault == 'outer_coal':
        backend.state.factory['coal_supply']['force_index'] += 1
    elif fault == 'inner_solid':
        backend.state.factory['solid_routes']['force_index'] += 1
    before, failures, calls = deepcopy(state), deepcopy(loop.memory.failures), deepcopy(backend.calls)
    loop._observe()
    assert backend.calls == calls
    if fault == 'none':
        assert loop.memory.solid_funding is None
        assert loop.memory.failures[before['key'] + ':kit'] == 2
        assert loop.memory.failures['unrelated'] == 1
        loop._observe()
        assert sum(e['kind'] == 'solid_kit_abandoned' for e in loop.memory.history) == 1
    else:
        assert loop.memory.status == 'uncertain'
        assert loop.memory.solid_funding == before
        assert loop.memory.failures == failures


@pytest.mark.parametrize('fault', ['none', 'uncertain', 'inner_solid', 'resume_uncertain'])
def test_coal_expiry_preserves_holds_while_ownership_is_uncertain(tmp_path, fault):
    backend = Backend(); loop = controller(backend, tmp_path)
    assert loop.step()['verified']
    state = loop.memory.coal_funding
    state['deadline_tick'] = state['started_tick'] + 1
    backend.state.tick = max(backend.state.tick, state['deadline_tick'])
    backend.state.factory['coal_supply']['tick'] = backend.state.tick
    backend.state.factory['solid_routes']['tick'] = backend.state.tick
    loop.memory.failures['unrelated'] = 1
    if fault in {'uncertain', 'resume_uncertain'}:
        loop.memory.status = 'uncertain'
    elif fault == 'inner_solid':
        backend.state.factory['solid_routes']['force_index'] += 1
    before, failures, calls = deepcopy(state), deepcopy(loop.memory.failures), deepcopy(backend.calls)
    if fault == 'resume_uncertain':
        loop._save()
        loop = controller(backend, tmp_path, resume=True)
    loop._observe()
    assert backend.calls == calls
    if fault == 'none':
        assert loop.memory.coal_funding is None
        assert loop.memory.failures[before['key']] == 2
        assert loop.memory.failures['unrelated'] == 1
        loop._observe()
        assert sum(e['kind'] == 'coal_kit_abandoned' for e in loop.memory.history) == 1
    else:
        assert loop.memory.status == 'uncertain'
        assert loop.memory.coal_funding == before
        assert loop.memory.failures == failures
