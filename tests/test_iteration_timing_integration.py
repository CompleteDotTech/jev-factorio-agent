"""Actual controller/writer/native-wrapper boundaries on deterministic fixtures."""
from copy import deepcopy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import time

import pytest

import jev_factorio.iteration_timing as timing
from jev_factorio.latency_report import analyze
from jev_factorio.research_log import ResearchLog, RunConfiguration, verify_run
from jev_factorio.causal_trace import CausalTrace
from jev_factorio.backends.fle import SessionRcon
from test_solid_route_integration import Backend, controller
from solid_routes_fixtures import ROUTE
from test_atomic_observation import setup


def test_real_controller_record_checkpoint_research_and_gap(tmp_path):
    backend=Backend();loop=controller(backend,tmp_path)
    loop.log_file=tmp_path/'gameplay.jsonl'
    with ResearchLog(tmp_path/'research',RunConfiguration('mock','hierarchical','deterministic'),
                     repo_dir=tmp_path,environ={}) as sink:
        loop._trace=CausalTrace(sink,'hierarchical')
        loop._trace.metrics=loop._performance
        with redirect_stdout(io.StringIO()):
            for _ in range(3):
                assert loop.step()['verified']
                timing.loop_sleep(loop,.001,lambda:time.sleep(.001))
    records=[json.loads(line) for line in loop.log_file.read_text().splitlines()]
    assert 'previous_iteration_timing' not in records[0]
    for index,record in enumerate(records[1:],1):
        previous=timing.validate_timing(record['previous_iteration_timing'])
        assert previous['iteration_index']==index and previous['partition_complete']
        names=set(previous['phases'])
        assert {'record_construct','legacy_encode','legacy_write','record_console',
                'checkpoint','checkpoint_file_sync','checkpoint_directory_sync',
                'research_append','research_serialize','research_hash','research_write',
                'research_fsync','trace_capture','trace_emit','operational_state'} <= names
        assert previous['gap']['sleep_calls']==1
        assert previous['gap']['intentional_sleep_ns']['wall']>0
    report=analyze(loop.log_file)
    assert report['iteration_timing']['complete_iterations']==2
    assert report['iteration_timing']['records_without_prior_timing']==1
    assert report['counts']['loop_sleep:calls']==2
    assert report['counts']['checkpoint:written']>0
    assert report['counts']['checkpoint:bytes_written']>0
    assert verify_run(tmp_path/'research')['complete']
    assert len(backend.calls)==3 and loop.memory.pending is None
    assert len(loop.memory.solid_commitments[ROUTE]['parts'])==3
    public=json.dumps(report)
    assert backend.state.session_id not in public and 'private' not in public
    assert report['native_acceptance_proven'] is False


def test_gameplay_writer_uses_lf_byte_count_without_text_translation(tmp_path, monkeypatch):
    loop = controller(Backend(), tmp_path)
    path = tmp_path / 'gameplay.jsonl'
    loop.log_file = path
    original_open = Path.open
    observed_newline = []

    def capture_open(target, *args, **kwargs):
        if target == path:
            observed_newline.append(kwargs.get('newline'))
        return original_open(target, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', capture_open)
    with redirect_stdout(io.StringIO()):
        assert loop.step()['verified']

    assert observed_newline == ['\n']
    assert b'\r\n' not in path.read_bytes()


def test_atomic_observation_nested_adapters_only_one_logical_io(monkeypatch):
    backend,native,payload,calls=setup(monkeypatch,craft=True)
    backend._instance.rcon_client=SessionRcon(backend._instance.rcon_client)
    ledger=timing.Ledger();token=timing._CURRENT.set(ledger)
    try:
        with timing.span('iteration'):
            result=backend.observe()
    finally:timing._CURRENT.reset(token)
    summary=ledger.snapshot(1,'returned')
    assert summary['partition_complete'] and len(calls)==1
    assert summary['native_io']['command_calls']==1
    assert summary['native_io']['failed_calls']==0
    assert summary['native_io']['response_bytes']>0
    assert summary['phases']['native_decode']['calls']>=1
    assert result.inventory=={'coal':8}


def test_session_wrapper_unknown_request_bytes_preserves_delegate():
    class Client:
        def send_command(self, command):return command
    client=SessionRcon(Client());ledger=timing.Ledger();token=timing._CURRENT.set(ledger)
    try:
        with timing.span('iteration'):
            assert client.send_command('fixture\ud800')=='fixture\ud800'
    finally:timing._CURRENT.reset(token)
    assert ledger.io['unknown_request_size_calls']==ledger.io['unknown_response_size_calls']==1


def test_process_cpu_bound_and_waiting_clocks_are_distinguishable():
    # Deliberately broad attribution assertion, not an elapsed-time speed gate.
    def measure(operation):
        ledger=timing.Ledger()
        with ledger.span('iteration'):operation()
        summary=ledger.snapshot(1,'returned')
        assert summary['partition_complete']
        return summary['totals_ns']
    def compute():
        stop=time.thread_time_ns()+20_000_000
        while time.thread_time_ns()<stop:sum(i*i for i in range(100))
    cpu=measure(compute);wait=measure(lambda:time.sleep(.04))
    assert cpu['thread_cpu']>=20_000_000 and wait['wall']>=35_000_000
    assert wait['thread_cpu']<cpu['thread_cpu']//2


@pytest.mark.parametrize('tamper',[lambda r:r.update(previous_iteration_timing={'secret':'payload'}),
    lambda r:r['previous_iteration_timing']['phases'].update(private={}),
    lambda r:r['previous_iteration_timing']['native_io'].update(failed_calls=999)])
def test_report_rejects_malformed_timing_without_echo(tmp_path,tamper):
    loop=controller(Backend(),tmp_path)
    with redirect_stdout(io.StringIO()):loop.step();record=loop.step()
    tamper(record);path=tmp_path/'malformed.jsonl';path.write_text(json.dumps(record)+'\n')
    with pytest.raises(ValueError,match='^Invalid latency record at line 1$'):analyze(path)


def test_report_rejects_duplicate_prior_iteration(tmp_path):
    loop=controller(Backend(),tmp_path)
    with redirect_stdout(io.StringIO()):loop.step();record=loop.step()
    path=tmp_path/'duplicate.jsonl';path.write_text((json.dumps(record)+'\n')*2)
    with pytest.raises(ValueError,match='line 2'):analyze(path)
