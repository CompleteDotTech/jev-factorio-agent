import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from jev_factorio.backends.craft_jobs import CraftJobFactory
from jev_factorio.backends.input_routes import InputRouteFactory
from jev_factorio.backends.output_buffers import OutputBufferFactory
from jev_factorio.telemetry import validate_phase


CASES = [
    ("factory_craft_job", {"recipe": "science", "batches": 1, "receipt": "request-1"},
     ["transfer_rpc"], ["begin_craft_job"]),
    ("factory_input_build", {"source": "recipe:iron-plate", "layout": "layout-1",
                             "part": "inserter", "receipt": "request-1", "reserve_belts": 0},
     ["entity_lookup", "approach", "transfer_rpc"],
     ["prepare_input_route", "build_input_route"]),
    ("factory_buffer_build", {"source": "recipe:iron-plate", "layout": "layout-1",
                              "part": "chest", "receipt": "request-1"},
     ["entity_lookup", "approach", "transfer_rpc"],
     ["prepare_output_buffer", "build_output_buffer"]),
]


@pytest.fixture
def decorated(monkeypatch):
    env = ModuleType("fle.env")
    env.Position = SimpleNamespace
    monkeypatch.setitem(sys.modules, "fle.env", env)
    native = Mock()
    native.backend._native_attachment = None
    native.call.return_value = json.dumps(
        {"position": {"x": 1, "y": 2}, "name": "burner-inserter"})
    factory = InputRouteFactory(OutputBufferFactory(CraftJobFactory(native)))
    return factory, native


@pytest.mark.parametrize("action,parameters,stages,calls", CASES)
def test_extension_traces_keep_native_operations(decorated, action, parameters, stages, calls):
    factory, native = decorated
    events = []
    factory.execute(action, parameters, trace=events.append)
    assert [(event["stage"], event["status"]) for event in events] == [
        (stage, status) for stage in stages for status in ("started", "returned")]
    for event in events:
        validate_phase(event)
    assert [call.args[0] for call in native.call.call_args_list] == calls
    assert native.backend._fair.approach.call_count == ("approach" in stages)
    native.observe.assert_not_called()
    native.execute.assert_not_called()


def test_stacked_extensions_forward_the_same_trace(decorated):
    factory, native = decorated
    events = []
    trace = events.append
    parameters = {"role": "furnace", "item": "coal", "quantity": 1, "receipt": "r1"}
    factory.execute("factory_insert", parameters, trace=trace)
    native.execute.assert_called_once_with("factory_insert", parameters, trace=trace)
    assert events == []


def test_untraced_delegation_retains_legacy_signature(decorated):
    factory, native = decorated
    factory.execute("factory_wait", {})
    native.execute.assert_called_once_with("factory_wait", {})


@pytest.mark.parametrize("action,parameters,stages,calls", CASES)
def test_rpc_failure_is_traced_without_retry(decorated, action, parameters, stages, calls):
    factory, native = decorated
    failure = TimeoutError("private transport details")

    def call(name, *args):
        if name == calls[-1]:
            raise failure
        return json.dumps({"position": {"x": 1, "y": 2}, "name": "burner-inserter"})

    native.call.side_effect = call
    events = []
    with pytest.raises(TimeoutError) as raised:
        factory.execute(action, parameters, trace=events.append)
    assert raised.value is failure
    assert events[-1]["stage"] == "transfer_rpc"
    assert events[-1]["status"] == "failed"
    assert events[-1]["error_code"] == "timeout"
    assert "private" not in json.dumps(events)
    assert [call.args[0] for call in native.call.call_args_list] == calls
    native.observe.assert_not_called()


@pytest.mark.parametrize("action,parameters,stages,calls", CASES)
def test_trace_write_failure_prevents_dispatch(decorated, action, parameters, stages, calls):
    factory, native = decorated

    def fail_trace(event):
        raise OSError("trace unavailable")

    with pytest.raises(OSError, match="trace unavailable"):
        factory.execute(action, parameters, trace=fail_trace)
    native.call.assert_not_called()
    native.backend._fair.approach.assert_not_called()
