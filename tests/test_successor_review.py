"""Review regressions for independent identities and fail-closed activation."""
import sys

import pytest

from jev_factorio.planning.successors import SuccessorPlanner
from jev_factorio.skills import Plan, Step
from test_successors import BASE, KIND, GROWTH, Backend, empty_state, site_offer
from test_successors_lua import runtime


@pytest.mark.parametrize('runtime', ['copper'], indirect=True)
def test_copper_path_commissions_and_proves_use_without_rebinding_legacy_furnace(runtime):
    runtime.execute('''
        flowing_successor()
        c.transfer(output.chest_role,"copper-plate",16,"trial-copper",true)
        c.begin_craft_job("copper-use","copper-cable",8)
        for i=1,8 do finish_one() end
        c.observe();produce(600)
        local row=c.observe().successors.sources["growth:copper-plate"]
        assert(row.phase=="preferred" and row.use.quantity==6 and row.use.recipe=="copper-cable")
        assert(c.entities["recipe:copper-plate"]==old and old.unit_number==500 and old.valid)
        assert(row.source_unit~=row.predecessor_unit and game.speed==1)
    ''')


def test_different_failed_step_ids_cannot_evade_project_failure_budget(monkeypatch):
    from jev_factorio.input_controller import InputRouteMixin
    state = empty_state();site = site_offer(state)
    backend = Backend(state)
    loop = KIND(backend, policy='deterministic', target='rocket_launch', factory_scheduling='ready-work')
    loop.memory = loop.memory_type(state.session_id, 'rocket_launch', active_goal='rocket_launch', last_tick=300)
    project = {'anchor': site['anchor'], 'predecessor_unit': 500, 'source_unit': 0,
               'started_tick': 300, 'deadline_tick': 216300, 'status': 'active'}
    loop.memory.successor_projects[GROWTH] = project
    loop.memory.failures['successor:' + GROWTH + ':gather-a'] = 1
    loop.memory.failures['successor:' + GROWTH + ':gather-b'] = 1
    primary = Plan('ordinary', 'rocket_launch', 'Continue prior production', (Step('factory_wait', 'crafting_idle'),))
    monkeypatch.setattr(InputRouteMixin, '_compile_candidates', lambda self, observed: ([primary], ''))
    plans, _ = loop._compile_candidates(state)
    assert plans == [primary] and project['status'] == 'paused'
    assert loop.memory.failures['successor:' + GROWTH + ':gather-a'] == 1
    assert loop.memory.failures['successor:' + GROWTH + ':gather-b'] == 1
    assert loop.memory.failures['successor:' + GROWTH] >= 2
    assert backend.calls == []


def test_cli_rejects_nonidle_upgrade_before_calling_make_backend(monkeypatch, tmp_path):
    import jev_factorio.main as main
    path = tmp_path / 'memory.json'
    memory = BASE.memory_type('s', 'rocket_launch', active_goal='rocket_launch', last_tick=300)
    memory.active_plan = Plan('retained', 'rocket_launch', 'Keep pending intent', (Step('factory_wait', 'crafting_idle'),)).to_dict()
    memory.save(path)
    previous = path.read_bytes()
    monkeypatch.setattr(main, 'load_dotenv', lambda **kw: None)
    def forbidden(*args, **kwargs): raise AssertionError('make_backend must not run for invalid opt-in')
    monkeypatch.setattr(main, 'make_backend', forbidden)
    monkeypatch.setattr(sys, 'argv', ['jev-factorio', '--backend', 'fle', '--controller', 'hierarchical',
        '--factory-scheduling', 'ready-work', '--background-work', '--furnace-output-buffers',
        '--furnace-input-belts', '--ore-side-successors', '--resume', '--resume-controller',
        '--checkpoint', str(path), '--tick-seconds', '1', '--policy', 'deterministic'])
    with pytest.raises(SystemExit) as error: main.cli()
    assert error.value.code == 2 and path.read_bytes() == previous
