"""Real controller context/restart paths with deterministic native-like snapshots."""
from copy import deepcopy

from test_fuel_history import bind
from test_fuel_reserves import plan_with_history
from test_input_route_integration import RouteLoop, controller
from test_maintenance_progress import progress_scenario


def test_controller_reads_fuel_history_without_extra_backend_observations(tmp_path, monkeypatch):
    backend, _ = progress_scenario()
    bind(backend.state)
    loop = controller(backend, tmp_path, kind=RouteLoop)
    original = backend.observe
    calls = []
    def observed():
        calls.append(backend.state.tick)
        return original()
    monkeypatch.setattr(backend, 'observe', observed)
    for tick, fuel in ((300, 3), (600, 2), (900, 1)):
        backend.state.tick = tick
        for name in ('input_routes', 'output_buffers'):
            backend.state.factory[name]['tick'] = tick
        for role in ('input:drill', 'input:inserter'):
            backend.state.factory['entities'][role]['fuel']['coal'] = fuel
        snapshot = loop._observe()
    assert calls == [300, 600, 900]
    assert len(snapshot._fuel_service_history['rates']) == 2
    assert backend.calls == []
    # The derived history is intentionally not part of durable campaign state.
    assert 'fuel_service_history' not in (tmp_path / 'state.json').read_text()


def test_current_failure_context_is_copied_and_not_a_second_authoritative_ledger(tmp_path):
    backend, _ = progress_scenario()
    loop = controller(backend, tmp_path, kind=RouteLoop)
    loop.memory.failures['factory:factory_insert:input:drill'] = 2
    snapshot = deepcopy(backend.state)
    loop._compile_candidates(snapshot)
    assert snapshot._planner_failure_budgets == loop.memory.failures
    snapshot._planner_failure_budgets.clear()
    assert loop.memory.failures['factory:factory_insert:input:drill'] == 2


def test_actual_selection_budget_counts_prior_coal_quantities_and_survives_checkpoint(tmp_path):
    backend, _ = progress_scenario()
    loop = controller(backend, tmp_path, kind=RouteLoop)
    state, data, high = plan_with_history()
    state._fuel_service_history = {}
    from test_fuel_service_bounds import direct
    low = direct(state, data)
    loop.memory.failures[low.id] = 2
    assert loop._plan_failure_count(high) == 2
    loop._save()
    resumed = controller(backend, tmp_path, kind=RouteLoop, resume=True)
    resumed._observe()
    assert resumed._plan_failure_count(high) == 2
    assert resumed.memory.failures[low.id] == 2
    assert backend.calls == []
