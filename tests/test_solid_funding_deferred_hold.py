"""Top-level safety holds retain an invalidated kit until causal cleanup."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace
import pytest
from jev_factorio.operational_safety import StoragePressure
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop
from solid_routes_fixtures import row


@pytest.mark.parametrize('hold', ['storage_pressure', 'quiescent', 'quiescing', 'quiescence_timeout'])
@pytest.mark.parametrize('cause', ['deadline', 'endpoint'])
@pytest.mark.parametrize('failures', [0, 1, 2])
def test_deferred_kit_release_across_safety_hold(tmp_path, hold, cause, failures):
    loop, backend = kit_loop(tmp_path)
    loop._observe()
    def reject(): raise StoragePressure()
    loop._trace.admission_check = reject
    loop.step()  # Retain a real committed kit without dispatching it.
    loop._trace.admission_check = None
    proof = deepcopy(loop.memory.solid_funding)
    loop.memory.failures[proof['key'] + ':kit'] = failures
    initial = asdict(loop.memory)
    if cause == 'deadline':
        backend.state.tick = proof['deadline_tick']
        backend.state.factory['solid_routes']['tick'] = backend.state.tick
    else:
        row(backend.state)['layout'] = 'solid-layout:changed'
    loop._safety = SimpleNamespace(admission=lambda *a: hold, publish=lambda *a, **kw: None)
    records = [loop.step(), loop.step()]
    held = asdict(loop.memory)
    assert held['solid_funding'] == proof and held['active_plan'] == initial['active_plan']
    assert held['failures'][proof['key'] + ':kit'] == 2 and not backend.calls
    assert not funding_history_issues(initial, records, held)
    for field, value in [('outcome', 'unproven hold'), ('model_call', True), ('verified', True)]:
        broken = deepcopy(records)
        broken[0][field] = value
        assert funding_history_issues(initial, broken, held)
    broken = deepcopy(records)
    broken[0]['failure_budgets'][proof['key'] + ':kit'] = 3
    assert funding_history_issues(initial, broken, held)
    loop._safety = None
    records.append(loop.step())
    final = asdict(loop.memory)
    assert final['solid_funding'] is None and final['active_plan'] is None
    assert not funding_history_issues(initial, records, final)
