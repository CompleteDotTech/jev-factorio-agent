"""Migration and scheduling safety around the new additive capability."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from jev_factorio import successors
from jev_factorio.skills import Plan, Step
from jev_factorio.supervisor import Supervisor, SupervisorConfig
from test_successors import BASE, KIND, GROWTH, Backend, empty_state, site_offer, flowing_state, qualify
from test_successors_lua import runtime


def test_nonidle_legacy_migration_fails_before_backend_initialization(tmp_path):
    state = empty_state()
    path = tmp_path / 'memory.json'
    memory = BASE.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch', last_tick=300)
    memory.active_plan = Plan('pending-plan', 'rocket_launch', 'Retained nonmutating wait',
                              (Step('factory_wait', 'crafting_idle'),)).to_dict()
    memory.save(path)
    original = path.read_bytes()
    backend = Backend(state)
    def forbidden(): raise AssertionError('Backend must not initialize before capability preflight')
    backend.enable_factory = forbidden
    with pytest.raises(ValueError, match='idle, reconciled'):
        KIND(backend, policy='deterministic', target='rocket_launch', factory_scheduling='ready-work',
             checkpoint=str(path), resume_controller=True)
    assert path.read_bytes() == original


def test_unrelated_work_cannot_consume_held_construction_kit_but_can_use_science_surplus():
    state = empty_state();site = site_offer(state)
    state.inventory = successors.initial_kit(site, 'iron-ore')
    loop = KIND(Backend(state), policy='deterministic', target='rocket_launch', factory_scheduling='ready-work')
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', last_tick=state.tick)
    project = {'anchor': site['anchor'], 'predecessor_unit': 500, 'source_unit': 0,
               'started_tick': 300, 'deadline_tick': 216300, 'status': 'active'}
    loop.memory.successor_projects[GROWTH] = project
    def permitted(cost):
        step = Step('factory_craft', 'inventory', 'logistic-science-pack', 1, costs={'transport-belt': cost},
                    parameters={'recipe': 'logistic-science-pack', 'batches': cost})
        plan = Plan('ordinary-science', 'rocket_launch', 'Use only surplus', (step,))
        return loop._investment_step_allowed(plan, step, state)
    assert permitted(20)
    assert not permitted(21)
    assert state.inventory['transport-belt'] == 23
    assert project['status'] == 'active'


def test_persistent_counter_stall_restarts_sampling_window_without_destroying_evidence(runtime):
    runtime.execute('''
        flowing_successor()
        local m=storage.successors.records[growth];local first=m.window.first_tick
        for i=1,4 do game.tick=game.tick+600;c.observe() end
        assert(m.window.first_tick>first and not m.qualification)
        assert(m.source_unit==new.unit_number and old.valid and new.valid)
    ''')


@pytest.mark.parametrize('field,value', [('max_observation_gap', 1801), ('last_progress_tick', 0)])
def test_qualification_requires_recent_progress_and_bounded_observation_gaps(field, value):
    state, row = flowing_state();qualify(state, row)
    row['qualification'][field] = value
    with pytest.raises(ValueError): successors.sources(state)


def config(tmp_path, **changes):
    values = dict(state_dir=tmp_path / 'supervision', checkpoint=tmp_path / 'checkpoint.json',
                  session_id='s', started_at=1000, repair_command=['false'], cwd=tmp_path,
                  factory_scheduling='ready-work', background_work=True,
                  furnace_output_buffers=True, furnace_input_belts=True, ore_side_successors=True)
    values.update(changes)
    return SupervisorConfig(**values)


def test_supervisor_explicit_flag_reaches_child_without_changing_legacy_default(tmp_path):
    supervisor = Supervisor(config(tmp_path))
    supervisor.state = {'cutoff': 99999999999}
    command = supervisor.gameplay_command()
    assert '--ore-side-successors' in command and '--resume' in command and '--resume-controller' in command
    legacy = Supervisor(config(tmp_path, ore_side_successors=False))
    legacy.state = supervisor.state
    assert '--ore-side-successors' not in legacy.gameplay_command()


@pytest.mark.parametrize('change', [{'background_work': False}, {'furnace_input_belts': False}, {'mining_outposts': True}])
def test_supervisor_rejects_unsupported_successor_compositions(tmp_path, change):
    with pytest.raises(ValueError): config(tmp_path, **change).validate()


def test_operational_repair_cannot_remove_retained_successor_projects_or_receipts(tmp_path):
    supervisor = Supervisor(config(tmp_path))
    supervisor.state = {'run_id': 'run', 'incident': {'incident_id': 'incident'}, 'attempt': 1}
    previous = {'session_id': 's', 'target': 'rocket_launch', 'status': 'running', 'pending': None,
        'active_plan': None, 'step_index': 0, 'reservations': {}, 'attempt_outcomes': [], 'failures': {'old': 2},
        'history': [], 'successor_schema': 1, 'successor_projects': {GROWTH: {'status': 'paused'}},
        'successor_receipts': {GROWTH: {'output': {'chest': {'paid': 1}}}}}
    current = deepcopy(previous)
    current['successor_projects'] = {}
    current['successor_receipts'] = {}
    supervisor.checkpoint = lambda: current
    supervisor.source_identity = lambda: ('commit', 'unchanged')
    report = tmp_path / 'repair.json'
    report.write_text(json.dumps({'status': 'repaired', 'kind': 'operational', 'session_id': 's',
        'checkpoint': str(supervisor.config.checkpoint.resolve()), 'evidence': ['synthetic test'],
        'operational_verified': True}))
    assert not supervisor.validate_repair(report, previous, ('commit', 'unchanged'))
