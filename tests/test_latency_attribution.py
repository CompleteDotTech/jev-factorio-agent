"""Clock/call-accounting fixtures, never proof of native gameplay latency."""
import json
import time

import pytest

from jev_factorio.observation import ObservationProfile
from jev_factorio.performance import PerformanceCounters
from jev_factorio.causal_trace import CausalTrace


class Clocks:
    wall = 0
    cpu = 0
    def advance(self, wall, cpu):
        self.wall += wall
        self.cpu += cpu


def profile_for(clock):
    return ObservationProfile(clock=lambda: clock.wall, cpu_clock=lambda: clock.cpu)


def test_nested_helper_rpc_partition_is_not_inclusive_sum():
    clocks = Clocks()
    profile = profile_for(clocks)
    def helper():
        clocks.advance(8, 3)
        assert profile.rpc('entities', lambda: (clocks.advance(10, 2), 'ok')[1], 5) == 'ok'
        clocks.advance(7, 4)
        return 42
    assert profile.subcall('entities', helper) == 42
    clocks.advance(5, 1)
    result = profile.summary()
    assert result['wall_partition_ns'] == {'rpc': 10, 'helpers': 15, 'decode': 0, 'unattributed': 5}
    assert result['process_cpu_partition_ns'] == {'rpc': 2, 'helpers': 7, 'decode': 0, 'unattributed': 1}
    assert result['subcalls']['entities']['total_ns'] == 25
    assert result['subcalls']['entities']['exclusive_ns'] == 15
    assert result['total_ns'] == sum(result['wall_partition_ns'].values()) == 30
    assert result['process_cpu_ns'] == 10
    assert result['transport_separation_available'] is False
    assert result['helper_retry_attempts'] is None
    assert result['native_timing_available'] is False


def test_failure_and_retry_calls_count_actual_wrapped_attempts_not_invented_network_work():
    clocks = Clocks()
    profile = profile_for(clocks)
    error = ValueError('private-native-secret')
    def fail():
        clocks.advance(4, 1)
        raise error
    def helper():
        with pytest.raises(ValueError) as caught:
            profile.rpc('other', fail)
        assert caught.value is error
        clocks.advance(10, 0)  # opaque helper backoff, not guessed to be network time
        return profile.rpc('other', lambda: (clocks.advance(6, 2), 'success')[1])
    assert profile.subcall('entities', helper) == 'success'
    result = profile.summary()
    assert result['calls']['other']['count'] == 2
    assert result['calls']['other']['failed'] == 1
    assert result['wall_partition_ns'] == {'rpc': 10, 'helpers': 10, 'decode': 0, 'unattributed': 0}
    assert 'private-native-secret' not in json.dumps(result)


def test_nested_same_kind_and_decode_do_not_double_count(monkeypatch):
    clocks = Clocks()
    profile = profile_for(clocks)
    loads = json.loads
    def decode(raw):
        clocks.advance(3, 2)
        return loads(raw)
    monkeypatch.setattr(json, 'loads', decode)
    def inner():
        clocks.advance(2, 1)
        return profile.decode('{}')
    def outer():
        clocks.advance(5, 2)
        return profile.rpc('player_state', inner)
    assert profile.rpc('campaign_snapshot', outer) == {}
    result = profile.summary()
    assert result['wall_partition_ns'] == {'rpc': 7, 'helpers': 0, 'decode': 3, 'unattributed': 0}
    assert sum(result['process_cpu_partition_ns'].values()) == result['process_cpu_ns'] == 5


def test_summary_is_detached_from_later_profile_calls():
    clocks = Clocks()
    profile = profile_for(clocks)
    profile.rpc('other', lambda: clocks.advance(1, 1))
    old = profile.summary()
    profile.rpc('other', lambda: clocks.advance(1, 1))
    assert old['calls']['other']['count'] == 1


def test_cpu_and_waiting_fixtures_have_different_cpu_accounting():
    # Isolate attribution semantics from process-global activity in other tests.
    # A sleeping thread does not imply that its entire process consumed no CPU.
    wait_clock, work_clock = Clocks(), Clocks()
    waiting, working = profile_for(wait_clock), profile_for(work_clock)
    waiting.subcall('other', lambda: wait_clock.advance(40_000_000, 0))
    working.subcall('other', lambda: work_clock.advance(20_000_000, 20_000_000))
    wait_result, work_result = waiting.summary(), working.summary()
    assert wait_result['subcalls']['other']['total_ns'] == 40_000_000
    assert wait_result['process_cpu_ns'] == 0
    assert work_result['subcalls']['other']['total_ns'] == 20_000_000
    assert work_result['process_cpu_ns'] == 20_000_000


def test_process_cpu_can_advance_while_the_observed_helper_waits():
    # Model other Python/native threads running during this helper's wait.
    # Retain process cost without inventing a helper/network attribution.
    clock = Clocks()
    profile = profile_for(clock)
    profile.subcall('other', lambda: clock.advance(40_000_000, 60_000_000))
    result = profile.summary()
    assert result['process_cpu_ns'] == 60_000_000
    assert result['process_cpu_partition_ns']['helpers'] == 60_000_000
    assert result['wall_partition_ns']['helpers'] == 40_000_000
    assert result['transport_separation_available'] is False


@pytest.mark.parametrize('operation', ['wait', 'work'])
def test_real_clock_accounting_stays_within_external_process_bounds(operation):
    # Real-clock wiring check without a cross-workload latency/CPU ranking.
    wall_before, cpu_before = time.perf_counter_ns(), time.process_time_ns()
    profile = ObservationProfile()
    callback = (lambda: time.sleep(0.002)) if operation == 'wait' else (lambda: sum(range(20_000)))
    profile.subcall('other', callback)
    result = profile.summary()
    cpu_after, wall_after = time.process_time_ns(), time.perf_counter_ns()
    assert 0 <= result['total_ns'] <= wall_after - wall_before
    assert 0 <= result['process_cpu_ns'] <= cpu_after - cpu_before
    assert result['subcalls']['other']['count'] == 1
    assert sum(result['wall_partition_ns'].values()) == result['total_ns']
    assert sum(result['process_cpu_partition_ns'].values()) == result['process_cpu_ns']


def test_trace_operation_capture_and_emission_are_distinct_costs():
    class Sink:
        def emit(self, event_type, payload):
            pass
    trace = CausalTrace(Sink(), 'fixture')
    trace.metrics = PerformanceCounters()
    token = object()
    assert trace.call('observation', lambda: token, result=lambda value: {'ok': value is token}) is token
    result = trace.metrics.snapshot()
    assert set(result['calls']) == {'observation', 'trace_capture', 'trace_emit'}
    assert set(result['cpu_calls']) == set(result['calls'])
    assert result['durations_are_inclusive'] is True


def test_primary_operation_failure_is_preserved_if_trace_emission_also_fails():
    class Sink:
        def emit(self, event_type, payload):
            raise OSError('private-path')
    trace = CausalTrace(Sink(), 'fixture')
    trace.metrics = PerformanceCounters()
    error = ValueError('private-error')
    with pytest.raises(ValueError) as caught:
        trace.call('observation', lambda: (_ for _ in ()).throw(error))
    assert caught.value is error
    result = trace.metrics.snapshot()
    assert result['calls']['observation']['failed'] == 1
    assert result['calls']['trace_emit']['failed'] == 1
    assert 'private' not in json.dumps(result)
