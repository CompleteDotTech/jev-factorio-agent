"""Synthetic bounded scheduling and progress regressions; no native acceptance."""
from copy import deepcopy
from dataclasses import asdict
import json
from types import SimpleNamespace as NS

import pytest

from jev_factorio.campaign_progress import ProgressMonitor, ReplenishmentHistory
from jev_factorio.planning.ready_work import ReadyWorkPlanner
from jev_factorio.planning.scheduling import (research_schedule, measured_lead, future_research_eligibility,
                                            future_research_demands, current_research_supply)
from jev_factorio.planning.demand import SupplyLedger
from jev_factorio.planning.service_visits import service_visit
from jev_factorio.planning.background_work import independent_candidates
from test_deadline_scheduling import scenario
from test_factory import snapshot, machine
from test_phase2_automation import lab_fixture


class Clock:
    def __init__(self): self.value = 0
    def __call__(self): return self.value


def measured(state, lead=100, samples=2):
    return {'schema': 1, 'session_id': state.session_id, 'samples': samples,
            'lead_ticks': lead, 'observed_tick': state.tick, 'basis': 'demand_to_verified_lab_receipt'}


def test_lead_time_reserves_are_opt_in_and_bounded_by_stack_and_remaining():
    state, data = scenario(available=0, energy=60)
    state._measured_replenishment = {'red': measured(state, 216000)}
    assert research_schedule(state, data)[0]['amount'] == 20
    state._lead_time_supply = True
    data.stack_sizes['red'] = 75
    row = research_schedule(state, data)[0]
    assert row['target'] == row['amount'] == 75
    assert row['measured_lead_ticks'] == 216000
    state.factory['research_progress'] = .99
    assert research_schedule(state, data)[0]['amount'] <= 2  # floating boundary rounds conservatively


@pytest.mark.parametrize('field,value', [('session_id','other'), ('samples',True), ('samples',0),
    ('lead_ticks',True), ('lead_ticks',0), ('lead_ticks',216001), ('lead_ticks',float('nan')),
    ('observed_tick',True), ('observed_tick',999999), ('observed_tick',-216001), ('basis','estimate')])
def test_unverified_stale_or_invalid_lead_samples_do_not_drive_policy(field, value):
    state, _ = scenario()
    row = measured(state)
    row[field] = value
    state._measured_replenishment = {'red': row}
    assert measured_lead(state, 'red') is None


def test_small_carried_current_science_is_delivered_before_gathering_larger_reserve():
    state, data, _, _ = lab_fixture()
    state._lead_time_supply = True
    state.inventory = {'red': 1}
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    plan = planner.plan()
    assert plan.steps[0].action == 'factory_insert'
    assert plan.steps[0].parameters['item'] == 'red'
    assert plan.steps[0].parameters['quantity'] == 1
    assert plan.materials['scheduling']['kind'] == 'resupply'


@pytest.mark.parametrize('condition', ['unbound','disconnected','unpowered','crafting','empty_boiler'])
def test_resupply_does_not_bypass_binding_power_crafting_or_boiler_priority(condition):
    state, data, _, _ = lab_fixture()
    state._lead_time_supply = True
    if condition == 'unbound': state.factory['player_bound'] = False
    if condition == 'disconnected': state.factory['player_connected'] = False
    if condition == 'unpowered': state.factory['entities']['utility:lab']['energy'] = 0
    if condition == 'crafting': state.factory['crafting_queue'] = 1
    if condition == 'empty_boiler': state.factory['entities']['utility:boiler'] = machine('boiler')
    assert current_research_supply(ReadyWorkPlanner(data, state, 'rocket_launch')) is None


def test_science_resupply_respects_reservation_ledger_and_verified_per_step_receipts():
    state, data, planner, first = lab_fixture()
    state._lead_time_supply = state._campaign_diagnostics = True
    planner.ledger = SupplyLedger.capture(state, data, reserved={'green': 20})
    visit = service_visit(planner, first)
    assert len(visit.steps) == 1
    assert visit.steps[0].parameters['item'] == 'red'
    assert not visit.steps[0].satisfied(state)
    assert visit.materials['service_visit']['steps'] == 1
    assert state.inventory == {'red': 1, 'green': 20}


def lookahead():
    state, data = scenario(available=20, energy=3600)
    state._coverage_margin_lookahead = True
    state._measured_replenishment = {'red': measured(state, 100)}
    return state, data


def test_coverage_margin_requires_two_receipt_samples_for_every_science():
    state, data = lookahead()
    assert future_research_eligibility(state, data)['eligible']
    state._measured_replenishment['red']['samples'] = 1
    assert future_research_eligibility(state, data)['reason'] == 'insufficient_verified_replenishment_history'
    state._measured_replenishment['red']['samples'] = 2
    state._measured_replenishment['red']['lead_ticks'] = 100000
    assert future_research_eligibility(state, data)['reason'] == 'current_coverage_margin'


def test_partial_coverage_lookahead_only_collects_paid_nearby_next_science():
    state, data = lookahead()
    assert future_research_demands(state, data) == []  # would require consuming raw ingredients
    state.factory['entities']['recipe:green'] = machine(recipe='green', unit_number=21, output={'green': 5})
    assert future_research_demands(state, data) == [('green', 5)]
    plans = independent_candidates('rocket_launch', state, data)
    assert plans and all(p.steps[0].action == 'factory_extract' for p in plans)
    assert all(p.steps[0].parameters['item'] == 'green' for p in plans)
    assert all(p.steps[0].parameters['quantity'] <= 5 for p in plans)


@pytest.mark.parametrize('position', [{'x': 100, 'y': 0}, {'x': None, 'y': 0}, {'x': '1', 'y': 0},
                                     {'x': float('nan'), 'y': 0}, {'x': True, 'y': 0}, {}])
def test_unknown_or_distant_next_science_is_not_safe_lookahead(position):
    state, data = lookahead()
    state.factory['entities']['recipe:green'] = machine(recipe='green', output={'green': 5}, position=position)
    assert future_research_demands(state, data) == []


def test_disabled_lookahead_preserves_existing_fully_fed_gate():
    state, data = lookahead()
    state._coverage_margin_lookahead = False
    assert future_research_eligibility(state, data)['reason'] == 'current_research_not_fully_fed'
    state.factory['entities']['utility:lab']['input']['red'] = 100
    assert future_research_eligibility(state, data)['reason'] == 'current_research_fully_fed'


def receipt(state, step, **changes):
    params = step.parameters
    state.factory['receipts'][params['receipt']] = {
        'role': params['role'], 'item': params['item'], 'quantity': params['quantity'],
        'extracting': False, 'unit_number': state.factory['entities'][params['role']]['unit_number'], **changes}


def test_replenishment_history_uses_only_unique_independently_verified_lab_receipts():
    state, data, planner, plan = lab_fixture()
    clock = Clock(); history = ReplenishmentHistory(clock)
    history.observe(state, data)
    step = plan.steps[0]
    state.tick += 600; clock.value = 10
    history.delivered(step, state, 'attempt-1')
    assert not history.deliveries
    receipt(state, step, unit_number=999)
    history.delivered(step, state, 'attempt-1')
    assert not history.deliveries
    receipt(state, step)
    # Post-dispatch observation may already show a full lab; it must retain the pending lead sample.
    state.factory['entities']['utility:lab']['input']['red'] = 100
    history.observe(state, data)
    history.delivered(step, state, 'attempt-1')
    history.delivered(step, state, 'attempt-1')
    assert history.deliveries == {'red': 1}
    assert history.evidence(state.tick)['red']['lead_ticks'] == 600
    assert history.evidence(state.tick)['red']['samples'] == 1
    state.session_id = 'another-session'
    history.observe(state, data)
    assert not history.deliveries and not history.evidence(state.tick)


def test_no_progress_is_alerted_after_declared_window_even_when_running():
    state = snapshot(); state.factory.update(research='study', research_progress=0, consumed={})
    clock = Clock(); monitor = ProgressMonitor(clock=clock)
    monitor.update(state, None, 'factory_insert', 'running')
    clock.value = 1799; state.tick += 1
    assert not monitor.update(state, None, 'factory_insert', 'running')['no_science_consumption_alert']
    clock.value = 1800; state.tick += 1
    row = monitor.update(state, None, 'factory_insert', 'running')
    assert row['window_complete'] and row['no_research_progress_alert']
    assert row['no_material_progress_alert'] and row['no_science_consumption_alert']
    assert row['science_consumed_per_actor_minute'] == 0


def test_useful_native_consumption_not_action_count_drives_rates():
    state = snapshot(); state.factory.update(research='study', research_progress=0, consumed={})
    clock = Clock(); monitor = ProgressMonitor(clock=clock)
    monitor.update(state, None, 'factory_insert', 'running', delivered={})
    state.factory['consumed']['chemical-science-pack'] = 30
    state.factory['research_progress'] = .1
    clock.value = 1800; state.tick += 108000
    row = monitor.update(state, None, 'factory_extract', 'running', delivered={'chemical-science-pack': 40})
    assert row['science_consumed_per_actor_minute'] == 1
    assert row['science_consumed_per_decision'] == 30
    assert row['science_delivered_per_actor_minute'] == pytest.approx(4/3)
    assert not row['no_science_consumption_alert'] and row['research_progress']


@pytest.mark.parametrize('reset', ['session','tick','clock','produced','consumed'])
def test_progress_resets_epochs_instead_of_counting_negative_or_foreign_progress(reset):
    state = snapshot(); state.factory.update(consumed={'red': 4}, produced={'iron': 2})
    clock = Clock(); monitor = ProgressMonitor(clock=clock)
    monitor.update(state, None, 'observe', 'running')
    clock.value = 1800; state.tick += 1
    if reset == 'session': state.session_id = 'new'
    if reset == 'tick': state.tick = 0
    if reset == 'clock': clock.value = -1
    if reset == 'produced': state.factory['produced'] = {'iron': 1}
    if reset == 'consumed': state.factory['consumed'] = {}
    row = monitor.update(state, None, 'observe', 'running')
    assert row['counter_epoch_reset'] and not row['window_complete']
    assert not row['no_material_progress_alert']


@pytest.mark.parametrize('counter', [None, [], {'x': -1}, {'x': True}, {'x': float('nan')}])
def test_unknown_telemetry_is_not_reported_as_zero_consumption(counter):
    state = snapshot(); state.factory['consumed'] = counter
    clock = Clock(); monitor = ProgressMonitor(clock=clock)
    monitor.update(state, None, 'observe', 'running')
    clock.value = 1800
    row = monitor.update(state, None, 'observe', 'running')
    assert row['science_consumed_per_actor_minute'] is None
    assert not row['no_science_consumption_alert']


def test_repair_activity_and_fish_are_not_hidden_by_running_or_fake_victory():
    state = snapshot(world_kind='fle', victory=True)
    monitor = ProgressMonitor()
    assert not monitor.update(state, None, 'factory_launch_fish', 'running')['native_victory']
    assert monitor.update(state, None, 'verify', 'uncertain')['activity'] == 'repair-required'
    state.victory_source = 'native:base-game-rocket-launch'
    assert monitor.update(state, None, 'verify', 'running')['native_victory']


def test_observation_and_inclusive_dispatch_times_are_not_double_counted():
    phases = [{'stage': name, 'status': 'returned', 'seconds': seconds} for name, seconds in
              [('observe',32), ('pre_dispatch_observe',32), ('post_dispatch_observe',32),
               ('dispatch',4), ('approach',3), ('transfer_rpc',1)]]
    assert ProgressMonitor().update(snapshot(), None, 'factory_insert', 'running', phases)['activity'] == 'observing'


@pytest.mark.parametrize('flags', [['--campaign-diagnostics'], ['--consolidated-observations','--controller','hierarchical'],
    ['--lead-time-supply','--controller','hierarchical'],
    ['--coverage-margin-lookahead','--controller','hierarchical']])
def test_invalid_cli_combinations_fail_before_backend_start(monkeypatch, flags):
    import jev_factorio.main as main
    monkeypatch.setattr(main, 'load_dotenv', lambda **kw: None)
    monkeypatch.setattr(main, 'make_backend', lambda *args, **kw: pytest.fail('must reject before backend start'))
    monkeypatch.setattr('sys.argv', ['jev-factorio', '--backend','mock', *flags])
    with pytest.raises(SystemExit) as error: main.cli()
    assert error.value.code == 2


def test_treatment_configuration_is_strict_and_legacy_compatible():
    from jev_factorio.research_log import RunConfiguration, _configuration
    config = asdict(RunConfiguration(backend='mock', controller='hierarchical', policy='deterministic',
                                    target='bootstrap_mining', tick_seconds=0, confidence_floor=.45))
    _configuration(config)
    for key in ('campaign_diagnostics','profile_observations','consolidated_observations',
                'lead_time_supply','coverage_margin_lookahead'):
        invalid = {**config, key: 'true'}
        with pytest.raises(ValueError): _configuration(invalid)
    legacy = {k:v for k,v in config.items() if k not in {'campaign_diagnostics','profile_observations',
        'consolidated_observations','lead_time_supply','coverage_margin_lookahead'}}
    _configuration(legacy)


def test_collection_only_gate_cannot_expand_into_craft_or_construction(monkeypatch):
    from jev_factorio.planning.scheduling import future_research_plan
    state, data = lookahead()
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    for action in ('factory_craft', 'factory_place', 'factory_gather', 'factory_insert'):
        fake = NS(steps=[NS(action=action, parameters={'item':'green'})])
        monkeypatch.setattr(planner, '_need', lambda *args: fake)
        assert future_research_plan(planner, 'green', 5) is None


def test_collection_only_gate_cannot_add_service_or_speculative_frontier(monkeypatch):
    from jev_factorio.planning.factory import FactoryPlanner
    state, data = lookahead()
    state.inventory = {'iron-plate': 100, 'coal': 50}
    state.factory['entities']['recipe:green'] = machine(recipe='green', output={'green': 5})
    planner = ReadyWorkPlanner(data, state, 'rocket_launch')
    wait = planner._wait('research_progress', 'study', .1, timeout=3600, identity='waiting')
    monkeypatch.setattr(FactoryPlanner, 'plan', lambda self: wait)
    monkeypatch.setattr(planner, '_candidate_worker', lambda: pytest.fail('no speculative ingredient work'))
    monkeypatch.setattr(planner, '_capacity_work', lambda plan: pytest.fail('no extra capacity work'))
    plans = planner.candidates()
    assert len(plans) == 1 and len(plans[0].steps) == 1
    assert plans[0].steps[0].action == 'factory_extract'
    assert plans[0].materials['collection_only_lookahead'] is True
    assert service_visit(planner, plans[0]) == plans[0]


def test_unknown_research_progress_is_not_a_no_progress_alert():
    state = snapshot(); state.factory.update(research='study', consumed={})
    clock = Clock(); monitor = ProgressMonitor(clock=clock)
    monitor.update(state, None, 'observe', 'running')
    clock.value = 1800
    result = monitor.update(state, None, 'observe', 'running')
    assert not result['research_progress_known'] and not result['no_research_progress_alert']
