"""Post-merge #114 review regressions; deterministic fixtures, not native proof."""
from copy import deepcopy
import json

import pytest

from jev_factorio import solid_routes as routes
from jev_factorio.controller import HierarchicalLoop
from test_solid_route_integration import Backend, controller
from test_solid_investment import scenario, service_history, offers, make_loop, policy
from solid_routes_fixtures import TARGET, SOURCE, row


def retained(tmp_path):
    backend = Backend()
    loop = controller(backend, tmp_path)
    loop.memory.active_goal = 'rocket_launch'
    loop.memory.failures['retained-project'] = 2
    loop._observe()
    saved = backend.checkpoint.read_bytes()
    resumed = controller(backend, tmp_path, resume=True)
    return backend, resumed, saved


@pytest.mark.parametrize('change', ['valid-history', 'whitespace', 'removed', 'invalid-schema'])
def test_checkpoint_replaced_during_first_observation_refuses_without_actuation(tmp_path, change):
    backend, resumed, saved = retained(tmp_path)
    replacement = json.loads(saved)
    replacement['failures'] = {'replacement-project': 1}
    if change == 'invalid-schema': replacement['version'] = 999
    replacement_bytes = json.dumps(replacement).encode() if change != 'whitespace' else saved + b' '
    def replace_during_read():
        if change == 'removed': backend.checkpoint.unlink()
        else: backend.checkpoint.write_bytes(replacement_bytes)
    backend.before_observe = replace_during_read
    with pytest.raises(ValueError, match='Checkpoint changed'):
        resumed.step()
    assert backend.calls == []
    assert resumed.memory is None
    assert resumed._persistence_failed and resumed._solid_fault
    assert (not backend.checkpoint.exists() if change == 'removed'
            else backend.checkpoint.read_bytes() == replacement_bytes)
    reads = backend.observations
    # A caller cannot swallow the first exception and accidentally keep acting.
    with pytest.raises(RuntimeError, match='reconstruct|Reconstruct'):
        resumed.step()
    with pytest.raises(RuntimeError): resumed._save()
    assert backend.observations == reads and backend.calls == []


def test_backend_exception_after_replacement_does_not_hide_the_checkpoint_fault(tmp_path):
    backend, resumed, saved = retained(tmp_path)
    def replace_and_raise():
        backend.checkpoint.write_bytes(saved + b' ')
        raise TimeoutError('synthetic observation timeout')
    backend.before_observe = replace_and_raise
    with pytest.raises(ValueError, match='Checkpoint changed'):
        resumed.step()
    assert resumed.memory is None and resumed._persistence_failed and backend.calls == []
    assert backend.checkpoint.read_bytes() == saved + b' '


def test_same_checkpoint_bytes_replaced_at_new_inode_remain_compatible(tmp_path):
    backend, resumed, saved = retained(tmp_path)
    def same_bytes():
        replacement = tmp_path / 'same-bytes'
        replacement.write_bytes(saved)
        replacement.replace(backend.checkpoint)
    backend.before_observe = same_bytes
    resumed._observe()
    assert resumed.memory.failures == {'retained-project': 2}
    assert not resumed._persistence_failed and backend.calls == []


def test_resume_does_not_reopen_mutable_checkpoint_as_its_memory_source(tmp_path, monkeypatch):
    backend, resumed, saved = retained(tmp_path)
    # Preflight has already used the full composed loader. A later path read must
    # not allow an ABA writer to supply other ownership and then restore bytes.
    def mutable_reload(*args, **kwargs):
        pytest.fail('first observation reopened mutable checkpoint for memory')
    monkeypatch.setattr(type(resumed).memory_type, 'load', mutable_reload)
    resumed._observe()
    assert resumed.memory.failures == {'retained-project': 2}
    assert backend.calls == []


def test_preflight_validates_captured_bytes_during_temporary_path_swap(tmp_path, monkeypatch):
    backend, unused, saved = retained(tmp_path)
    other = json.loads(saved)
    other['failures'] = {'foreign-project': 9}
    foreign = json.dumps(other).encode()
    memory_type = type(unused).memory_type
    original = memory_type.from_bytes

    def swap_only_while_validating(cls, raw, session_id, target):
        assert raw == saved
        backend.checkpoint.write_bytes(foreign)
        try:
            return original(raw, session_id, target)
        finally:
            backend.checkpoint.write_bytes(saved)

    monkeypatch.setattr(memory_type, 'from_bytes', classmethod(swap_only_while_validating))
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed._solid_resume_memory.failures == {'retained-project': 2}
    assert backend.checkpoint.read_bytes() == saved
    resumed._observe()
    assert resumed.memory.failures == {'retained-project': 2}


def test_failure_after_provisional_pending_memory_cannot_write_diagnostics(tmp_path, monkeypatch):
    backend = Backend(); backend.ambiguous_placement = True
    loop = controller(backend, tmp_path)
    loop.step()
    assert loop.memory.pending is not None
    saved = backend.checkpoint.read_bytes()
    resumed = controller(backend, tmp_path, resume=True)

    from jev_factorio import capital_controller
    def reject_after_initialization(*args):
        raise ValueError('synthetic later validator rejection')
    monkeypatch.setattr(capital_controller, 'observe', reject_after_initialization)
    with pytest.raises(ValueError, match='later validator'):
        resumed._observe()
    assert resumed.memory is None and resumed._persistence_failed
    assert backend.checkpoint.read_bytes() == saved
    assert backend.calls and len(backend.calls) == 1


def test_replacement_after_inner_observer_returns_is_also_rejected(tmp_path, monkeypatch):
    backend, resumed, saved = retained(tmp_path)
    original = HierarchicalLoop._observe_snapshot
    def late_replace(self):
        snapshot = original(self)
        self.checkpoint.write_bytes(saved + b' ')
        return snapshot
    monkeypatch.setattr(HierarchicalLoop, '_observe_snapshot', late_replace)
    with pytest.raises(ValueError, match='Checkpoint changed'):
        resumed.step()
    assert resumed.memory is None and backend.calls == []
    assert backend.checkpoint.read_bytes() == saved + b' '


def test_unchanged_checkpoint_with_transient_observation_failure_can_retry(tmp_path):
    backend, resumed, saved = retained(tmp_path)
    def transient(): raise TimeoutError('synthetic read timeout')
    backend.before_observe = transient
    with pytest.raises(TimeoutError): resumed._observe()
    assert backend.checkpoint.read_bytes() == saved and not resumed._persistence_failed
    backend.before_observe = lambda: None
    resumed._observe()
    assert resumed.memory.failures == {'retained-project': 2}


@pytest.mark.parametrize('carried,held,needed', [(0,0,120),(40,0,80),(120,0,0),(200,0,0),(120,20,20),(120,120,120)])
def test_only_unreserved_carried_ingredients_reduce_haul_demand(carried, held, needed):
    state, data = scenario()
    state.inventory['iron-gear-wheel'] = carried
    demand, reason = policy.requirements(state, data, reserved={'iron-gear-wheel': held})
    assert reason == 'current_research_recipe_bill'
    assert demand['automation-science-pack']['iron-gear-wheel'] == needed
    assert state.inventory['iron-gear-wheel'] == carried
    assert state.factory['entities'][SOURCE]['output']['iron-gear-wheel'] == 120


@pytest.mark.parametrize('carried,target_stock,needed', [(20,20,80),(100,20,0),(0,120,0)])
def test_carried_and_destination_stock_are_not_counted_as_avoidable_hauling(carried, target_stock, needed):
    state, data = scenario()
    state.inventory['iron-gear-wheel'] = carried
    state.factory['entities'][TARGET]['input']['iron-gear-wheel'] = target_stock
    # Copper missing prevents queued-science forecast from obscuring this check.
    state.factory['entities'][TARGET]['input']['copper-plate'] = 0
    history = service_history(state)
    demand, _ = policy.requirements(state, data)
    value = policy.offer_value(row(state), state, data, demand, history)
    if needed:
        assert value['demand_units'] == needed and value['valued_units'] == needed
    else:
        assert value == {'eligible': False, 'reason': 'no_current_recipe_deficit'}


def test_carried_stock_defers_actual_controller_investment(tmp_path):
    loop, backend = make_loop(tmp_path)
    backend.state.inventory['iron-gear-wheel'] = 120
    result = loop.step()
    assert backend.calls == [] and not loop.memory.solid_commitments
    assert result['action'] != routes.COMMAND


def test_fresh_carried_stock_invalidates_an_earlier_selected_offer(tmp_path):
    loop, backend = make_loop(tmp_path)
    def change():
        if backend.observations == 2:
            backend.state.inventory['iron-gear-wheel'] = 120
    backend.before_observe = change
    result = loop.step()
    assert not result['verified'] and backend.calls == [] and loop.memory.pending is None


def test_source_stock_is_not_subtracted_as_if_already_carried():
    state, data = scenario(); history = service_history(state)
    plans, info = offers(state, data, history)
    assert len(plans) == 1
    assert next(iter(info['routes'].values()))['valued_units'] == 120


def test_shared_carried_stock_is_credited_once_across_recipes():
    state, data = scenario()
    state.factory['entities'][TARGET]['input'].clear()
    data.technologies['study']['ingredients'].append({'name':'logistic-science-pack','amount':1})
    from test_factory import recipe
    data.recipes['logistic-science-pack'] = recipe('logistic-science-pack', {'iron-gear-wheel': 1})
    state.inventory['iron-gear-wheel'] = 120
    # Forecast stock covers 240 gears, but only the carried half eliminates hauling.
    demand, _ = policy.requirements(state, data)
    total = sum(items.get('iron-gear-wheel',0) for items in demand.values())
    assert total == 120
    assert policy.requirements(state, data)[0] == demand  # No mutation across calls.


def test_transient_path_swap_cannot_replace_the_prevalidated_memory(tmp_path, monkeypatch):
    backend, resumed, saved = retained(tmp_path)
    original = type(resumed).memory_type.load
    changed = json.loads(saved)
    changed['failures'] = {'replacement-project': 1}
    def aba_loader(cls, path, session, target):
        path.write_text(json.dumps(changed))
        try:
            return original(path, session, target)
        finally:
            path.write_bytes(saved)
    monkeypatch.setattr(type(resumed).memory_type, 'load', classmethod(aba_loader))
    resumed._observe()
    assert resumed.memory.failures == {'retained-project': 2}
    assert backend.calls == []


@pytest.mark.parametrize('change', ['session', 'tick'])
def test_prevalidated_memory_still_requires_fresh_live_identity_and_time(tmp_path, change):
    backend, resumed, saved = retained(tmp_path)
    if change == 'session': backend.state.session_id = 'another-fixture-session'
    else: backend.state.tick = max(0, backend.state.tick - 1)
    with pytest.raises(ValueError, match='Session changed|tick regressed'):
        resumed.step()
    assert backend.calls == [] and backend.checkpoint.read_bytes() == saved


def test_paid_project_is_not_abandoned_when_inputs_arrive_in_inventory(tmp_path):
    loop, backend = make_loop(tmp_path)
    assert loop.step()['verified']
    initial = deepcopy(loop.memory.solid_commitments)
    backend.state.inventory['iron-gear-wheel'] = 120
    assert loop.step()['verified']
    assert len(backend.calls) == 2
    for key, commitment in initial.items():
        for part, receipt in commitment['parts'].items():
            assert loop.memory.solid_commitments[key]['parts'][part] == receipt
    assert loop.memory.failures == {}
