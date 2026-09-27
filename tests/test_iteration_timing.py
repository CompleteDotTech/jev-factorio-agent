"""Nested fake clocks, failure preservation and one-record-lag timing."""
import copy
import json
from types import SimpleNamespace as NS

import pytest

import jev_factorio.iteration_timing as timing


class Clock:
    def __init__(self): self.values=[0,0,0]
    def advance(self,wall,cpu=0,thread=0): self.values=[a+b for a,b in zip(self.values,(wall,cpu,thread))]
    @property
    def clocks(self): return tuple(lambda i=i:self.values[i] for i in range(3))


def ledger(clock):
    return timing.Ledger(clock=clock.clocks[0],cpu_clock=clock.clocks[1],thread_clock=clock.clocks[2])


def test_nested_inclusive_and_exclusive_times_reconcile():
    c=Clock();l=ledger(c)
    with l.span('iteration'):
        c.advance(2,1,1)
        with l.span('observe'):
            c.advance(3,2,1)
            with l.span('native_command'):c.advance(7,1,1)
            c.advance(4,1,1)
        c.advance(5,2,1)
    value=l.snapshot(1,'returned')
    assert value['partition_complete']
    assert value['totals_ns']=={'wall':21,'process_cpu':7,'thread_cpu':5}
    assert value['phases']['observe']['wall_inclusive_ns']==14
    assert value['phases']['observe']['wall_exclusive_ns']==7
    assert value['phases']['iteration']['wall_exclusive_ns']==7


def test_operation_exception_unchanged_and_context_restored():
    c=Clock();l=ledger(c);error=RuntimeError('secret fixture')
    token=timing._CURRENT.set(l)
    try:
        with pytest.raises(RuntimeError) as caught:
            with timing.span('iteration'):
                with timing.span('native_command'):
                    c.advance(10,2,1);raise error
        assert caught.value is error
    finally:timing._CURRENT.reset(token)
    value=l.snapshot(1,'error')
    assert value['phases']['native_command']['failed']==1
    assert 'secret' not in json.dumps(value) and timing._CURRENT.get() is None


def test_unknown_labels_do_not_expose_content():
    c=Clock();l=ledger(c)
    with l.span('iteration'):
        with l.span('private endpoint credential'):c.advance(1)
    assert set(l.snapshot(1,'returned')['phases'])=={'iteration','other'}


def test_clock_regression_invalidates_partition_not_operation():
    c=Clock();l=ledger(c)
    with l.span('iteration'):
        c.advance(10)
        with l.span('record'):c.advance(-5)
    value=l.snapshot(1,'returned')
    assert value['partition_complete'] is False and value['totals_ns'] is None and not value['phases']


def test_clock_failure_does_not_hide_primary_exception():
    def bad():raise ValueError('clock fault')
    l=timing.Ledger(clock=bad)
    with pytest.raises(TimeoutError):
        with l.span('iteration'):raise TimeoutError('primary')
    assert l.snapshot(1,'error')['partition_complete'] is False


def setup_loop(monkeypatch):
    c=Clock();original=timing.Ledger
    monkeypatch.setattr(timing,'Ledger',lambda:original(clock=c.clocks[0],cpu_clock=c.clocks[1],thread_clock=c.clocks[2]))
    class Loop:
        factory_scheduling='ready-work'
        @timing.profiled_iteration
        def step(self):
            with timing.span('record'):
                previous=timing.previous_timing(self)
                c.advance(10,2,1)
                return previous
    return Loop(),c


def test_prior_iteration_is_complete_only_when_next_step_starts(monkeypatch):
    loop,c=setup_loop(monkeypatch)
    assert loop.step() is None
    c.advance(3,1,1)
    assert timing.loop_sleep(loop,.000000020,lambda:c.advance(20,1,1)) is None
    c.advance(4,2,1)
    value=loop.step()
    timing.validate_timing(value)
    assert value['iteration_index']==1 and value['totals_ns']['wall']==10
    assert value['gap']['total_ns']=={'wall':27,'process_cpu':4,'thread_cpu':3}
    assert value['gap']['intentional_sleep_ns']=={'wall':20,'process_cpu':1,'thread_cpu':1}
    assert value['gap']['other_gap_ns']['wall']==7
    value['phases'].clear()
    assert timing.previous_timing(loop)['phases']  # returned evidence is detached


def test_failed_sleep_keeps_failure_and_measures_elapsed(monkeypatch):
    loop,c=setup_loop(monkeypatch);loop.step()
    def interrupted():c.advance(5);raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):timing.loop_sleep(loop,10,interrupted)
    value=loop.step()
    timing.validate_timing(value)
    assert value['gap']['sleep_calls']==value['gap']['sleep_failed']==1
    assert value['gap']['intentional_sleep_ns']['wall']==5


@pytest.mark.parametrize('tamper', [
    lambda x:x.update(extra='private'),
    lambda x:x['totals_ns'].update(wall=999),
    lambda x:x['phases'].update(private=x['phases']['record']),
    lambda x:x['gap']['other_gap_ns'].update(wall=999),
    lambda x:x['gap'].update(sleep_failed=10),
    lambda x:x.update(partition_complete=False),
])
def test_report_rejects_inconsistent_or_unbounded_evidence(monkeypatch,tamper):
    loop,c=setup_loop(monkeypatch);loop.step();value=loop.step();tamper(value)
    with pytest.raises(ValueError):timing.validate_timing(value)


def test_disabled_loop_does_not_read_clocks_or_change_return(monkeypatch):
    monkeypatch.setattr(timing,'Ledger',lambda:(_ for _ in ()).throw(AssertionError('disabled')))
    class Loop:
        @timing.profiled_iteration
        def step(self):return 'unchanged'
    assert Loop().step()=='unchanged'


def test_deep_nesting_remains_bounded():
    c=Clock();l=ledger(c)
    def nested(n):
        with l.span('other'):
            if n:nested(n-1)
            c.advance(1)
    nested(100)
    assert l.snapshot(1,'returned')['partition_complete'] is False
    assert not l.stack and len(l.rows)<=1


def test_thread_transition_invalidates_thread_cpu_partition(monkeypatch):
    c=Clock();l=ledger(c);owner=l.thread_id
    with l.span('iteration'):
        c.advance(10,2,1)
        monkeypatch.setattr(timing.threading,'get_ident',lambda:owner+1)
    assert not l.snapshot(1,'returned')['partition_complete']


def test_thread_transition_between_steps_keeps_gap_unknown(monkeypatch):
    loop,c=setup_loop(monkeypatch);loop.step()
    owner=loop._timing_pending['thread_id']
    monkeypatch.setattr(timing.threading,'get_ident',lambda:owner+1)
    result=loop.step()
    timing.validate_timing(result)
    assert result['partition_complete'] and not result['gap']['complete']
    assert 'thread_id' not in json.dumps(result)


def test_thread_transition_during_sleep_keeps_operation_result(monkeypatch):
    loop,c=setup_loop(monkeypatch);loop.step()
    owner=loop._timing_pending['thread_id']
    def moved():
        monkeypatch.setattr(timing.threading,'get_ident',lambda:owner+1)
        c.advance(10)
        return 'result'
    assert timing.loop_sleep(loop,1,moved)=='result'
    assert not loop.step()['gap']['complete']


def test_native_nested_wrappers_count_one_call_and_preserve_unicode():
    c=Clock();l=ledger(c);token=timing._CURRENT.set(l)
    try:
        with timing.span('iteration'):
            result=timing.native_io('native_command',lambda:timing.native_io(
                'native_command',lambda:'snow \u2603',request_bytes=10),request_bytes=10)
    finally:timing._CURRENT.reset(token)
    assert result=='snow \u2603'
    assert l.io['command_calls']==1 and l.io['request_bytes']==10
    assert l.io['response_bytes']==len(result.encode('utf-8'))
    assert timing._NATIVE_DEPTH.get()==0


def test_retry_fixture_counts_attempts_without_claiming_network_time():
    c=Clock();l=ledger(c);token=timing._CURRENT.set(l)
    error=TimeoutError('private contents')
    def failed():c.advance(5);raise error
    try:
        with timing.span('iteration'):
            with timing.span('fle_helper'):
                with pytest.raises(TimeoutError) as caught:
                    timing.native_io('native_command',failed,request_bytes=7)
                assert caught.value is error
                c.advance(10)  # Opaque helper backoff stays helper-exclusive wall.
                assert timing.native_io('native_command',lambda:'ok',request_bytes=7)=='ok'
    finally:timing._CURRENT.reset(token)
    value=l.snapshot(1,'returned')
    assert value['native_io']['command_calls']==2
    assert value['native_io']['failed_calls']==value['native_io']['unknown_response_size_calls']==1
    assert value['native_io']['request_bytes']==14 and value['native_io']['response_bytes']==2
    assert value['phases']['fle_helper']['wall_exclusive_ns']==10
    assert 'private' not in json.dumps(value) and timing._NATIVE_DEPTH.get()==0


@pytest.mark.parametrize('result',[None,17,{'payload':None},'\ud800'])
def test_unknown_response_size_preserves_return(result):
    c=Clock();l=ledger(c);token=timing._CURRENT.set(l)
    try:
        with timing.span('iteration'):
            assert timing.native_io('native_command',lambda:result) is result
    finally:timing._CURRENT.reset(token)
    assert l.io['unknown_response_size_calls']==1 and l.io['unknown_request_size_calls']==1


def test_batch_counts_one_logical_operation_not_commands_or_packets():
    c=Clock();l=ledger(c);token=timing._CURRENT.set(l)
    try:
        with timing.span('iteration'):
            result=timing.native_io('native_batch',lambda:{'a':'one','b':'two'},request_bytes=50)
    finally:timing._CURRENT.reset(token)
    assert result=={'a':'one','b':'two'}
    assert l.io['batch_calls']==1 and l.io['command_calls']==0 and l.io['response_bytes']==6
