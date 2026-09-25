"""Capability composition preserves existing native receipt and ambiguity barriers."""
from copy import deepcopy
from uuid import UUID
import json

import pytest

from jev_factorio.campaign_controller import campaign_loop_type
import test_causal_native_integration as native


@pytest.mark.parametrize('kind', ['background','buffer','route','combined'])
def test_diagnostics_preserve_gameplay_calls_and_reservations(tmp_path, monkeypatch, kind):
    monkeypatch.setattr('jev_factorio.background.uuid4', lambda: UUID(int=1))
    results = []
    for enabled in (False, True):
        if enabled:
            for name in ('HierarchicalLoop','BackgroundWorkLoop','CraftScenario','RouteScenario'):
                monkeypatch.setattr(native, name, campaign_loop_type(getattr(native, name)))
        backend, loop, calls = native.native_case(kind, tmp_path/f'{enabled}.json')
        record = loop.step()
        results.append((deepcopy(backend.calls), list(calls), deepcopy(backend.state),
            {key:record.get(key) for key in ('action','verified','status','outcome')},
            deepcopy(loop.memory.reservations), deepcopy(loop.memory.pending)))
        if enabled:
            assert record['campaign_treatment']['lead_time_supply'] is False
            assert record['investment_diagnostics']['retry_authorized'] is False
            assert 'campaign_progress' in record and 'host_pressure' in record
            assert '_measured_replenishment' not in json.dumps(record['state'])
    assert results[0] == results[1]


@pytest.mark.parametrize('kind', ['buffer','route','combined'])
def test_diagnostics_do_not_replay_or_reset_ambiguous_work(tmp_path, monkeypatch, kind):
    for name in ('HierarchicalLoop','BackgroundWorkLoop','CraftScenario','RouteScenario'):
        monkeypatch.setattr(native, name, campaign_loop_type(getattr(native, name)))
    backend, loop, calls = native.native_case(kind, tmp_path/'pending.json')
    if kind == 'buffer': backend.mode='fault'
    else: backend.fault_after_build=True
    first = loop.step()
    pending, reservations, budgets = deepcopy(loop.memory.pending), deepcopy(loop.memory.reservations), deepcopy(loop.memory.failures)
    second = loop.step()
    assert first['status'] == 'uncertain'
    assert second['campaign_progress']['activity'] == 'repair-required'
    assert loop.memory.pending == pending and loop.memory.reservations == reservations
    assert loop.memory.failures == budgets and len(backend.calls) == 1


def test_legacy_acceptance_gate_rejects_unreviewed_new_treatments(tmp_path):
    from test_native_acceptance import source_files, rewrite
    from jev_factorio.acceptance_capture import capture
    from jev_factorio.native_acceptance import analyze
    args, rows = source_files(tmp_path/'native-shaped-synthetic')
    for row in rows:
        row['campaign_treatment']={'schema':1,'consolidated_observations':True}
    rewrite(args, rows)
    capture(**args, environ={})
    report = analyze(args['output'])
    assert 'campaign_throughput_requires_separate_native_review' in report['issues']
    assert not report['measurement_checks_passed'] and not report['deployment_authorized']


from test_supervisor import supervisor


CAMPAIGN_FLAGS = ('campaign_diagnostics','profile_observations','consolidated_observations',
                  'lead_time_supply','coverage_margin_lookahead')


def test_supervisor_forwards_pinned_treatment_on_every_restart(supervisor):
    supervisor.config.factory_scheduling = 'ready-work'
    supervisor.config.lead_time_supply = True
    supervisor.config.coverage_margin_lookahead = True
    supervisor.config.consolidated_observations = True
    for _ in range(2):
        command = supervisor.gameplay_command()
        assert all('--'+name.replace('_','-') in command for name in CAMPAIGN_FLAGS)
        assert '--resume' in command and '--resume-controller' in command


@pytest.mark.parametrize('flag', CAMPAIGN_FLAGS)
def test_supervisor_does_not_change_existing_treatment_or_cutoff(supervisor, flag):
    original = supervisor.state_path.read_bytes()
    setattr(supervisor.config, flag, True)
    # Invalid combinations fail validation; valid ones fail immutable configuration comparison.
    with pytest.raises(ValueError): supervisor.initialize()
    assert supervisor.state_path.read_bytes() == original


def test_supervisor_default_configuration_remains_legacy_compatible(supervisor):
    command = supervisor.gameplay_command()
    assert not any('--'+name.replace('_','-') in command for name in CAMPAIGN_FLAGS)
    assert not set(CAMPAIGN_FLAGS) & supervisor.state['gameplay_configuration'].keys()


@pytest.mark.parametrize('flag', CAMPAIGN_FLAGS)
def test_supervisor_rejects_string_pilot_flags(supervisor, flag):
    setattr(supervisor.config, flag, 'false')
    with pytest.raises(ValueError, match='boolean'): supervisor.gameplay_command()
