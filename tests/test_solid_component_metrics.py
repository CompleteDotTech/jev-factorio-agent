"""Paid-component interruption boundaries in the composed controller, not an engine."""
from copy import deepcopy
import json

import pytest

from jev_factorio.causal_trace import CausalTrace
from jev_factorio.performance import PerformanceCounters
from jev_factorio.research_log import ResearchLogError
from solid_routes_fixtures import ROUTE, row
from test_causal_metrics_interruption import RecordingSink
from test_solid_investment import make_loop


@pytest.mark.parametrize('component_number', [1, 2, 3, 4])
@pytest.mark.parametrize('stage', ['action_returned', 'trace_capture', 'trace_emit'])
@pytest.mark.parametrize('exception_type', [RuntimeError, KeyboardInterrupt, SystemExit])
def test_counter_failure_after_paid_component_retains_exact_pending_and_prefix(
        tmp_path, monkeypatch, component_number, stage, exception_type):
    import jev_factorio.performance as performance

    loop, backend = make_loop(tmp_path)
    loop.memory.failures['unrelated-prior-project'] = 1
    for _ in range(component_number - 1):
        assert loop.step()['verified']
    assert len(backend.calls) == component_number - 1
    old_owned = deepcopy(loop.memory.solid_commitments)
    expected_error = exception_type('private fixture counter text')
    raised, before_effect = [], []
    original_execute = backend.execute

    def capture_prepared(action, parameters):
        before_effect.append(backend.checkpoint.read_bytes())
        return original_execute(action, parameters)

    class Counters(PerformanceCounters):
        def call(self, name, duration_ns, failed=False, *, cpu_ns=None):
            super().call(name, duration_ns, failed, cpu_ns=cpu_ns)
            if name == stage and len(backend.calls) == component_number and not raised:
                raised.append(True)
                raise expected_error

    backend.execute = capture_prepared
    monkeypatch.setattr(performance, 'PerformanceCounters', Counters)
    loop._trace = CausalTrace(RecordingSink(), 'paid-component-fixture')
    with pytest.raises(BaseException) as caught:
        loop.step()
    assert raised == [True]
    assert len(backend.calls) == component_number
    if exception_type is RuntimeError:
        assert isinstance(caught.value, ResearchLogError)
        assert 'private' not in str(caught.value)
    else:
        assert caught.value is expected_error
    assert loop._trace._failed is True
    assert backend.checkpoint.read_bytes() == before_effect[0]
    saved = json.loads(before_effect[0])
    assert saved['pending']['dispatch'] == 'prepared'
    assert saved['solid_commitments'] == old_owned
    assert saved['failures']['unrelated-prior-project'] == 1
    assert len(row(backend.state)['parts']) == component_number
    # The engine-shaped double is one paid component ahead of durable ownership;
    # retaining that ambiguity is required. Do not invent acknowledgement/flow.
    assert row(backend.state)['flow'] == {}
    restored = type(loop).memory_type.from_bytes(
        before_effect[0], backend.state.session_id, 'rocket_launch')
    assert restored.pending == loop.memory.pending
    calls, observations, failures = deepcopy(backend.calls), backend.observations, deepcopy(loop.memory.failures)
    loop._trace.metrics = None
    with pytest.raises(ResearchLogError):
        loop.step()
    assert backend.calls == calls and backend.observations == observations
    assert loop.memory.failures == failures
    assert backend.checkpoint.read_bytes() == before_effect[0]
