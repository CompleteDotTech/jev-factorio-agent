"""Actual controller/planner integration with synthetic snapshots, not a game run."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from jev_factorio.background import BackgroundWorkLoop
from jev_factorio.backends.input_routes import InputRouteFactory
from jev_factorio.buffer_controller import buffered_loop_type
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.input_controller import input_loop_type
from jev_factorio.input_routes import COMMAND, flow_complete, remaining
from jev_factorio.memory import CampaignMemory
from jev_factorio.planning.input_routes import InputRoutePlanner
from jev_factorio.skills import Plan, Step
from jev_factorio.state import GameSnapshot

from input_routes_fixtures import SOURCE, LAYOUT, build, commission, fixture, full, parameters, row
from test_factory import catalog, recipe


def game():
    state = GameSnapshot(**vars(fixture()), world_kind="mock", researched=[],
                         nearby_resources={"iron-ore": 1, "coal": 1})
    state.factory["fair_resource_targets"] = {
        name: {"name": name, "surface_index": 1, "position": {"x": 0.5, "y": 0.5}}
        for name in ("iron-ore", "coal")
    }
    return state


def native_catalog():
    value = catalog()
    value.recipes["logistic-science-pack"] = recipe("logistic-science-pack", {"transport-belt": 1})
    return value


@pytest.mark.parametrize("background", [False, True])
def test_reconstructed_controllers_reuse_nested_native_adapters(background):
    commands = []
    state = game()
    state.factory["craft_job_inventory"] = {"tick": state.tick, "items": {"coal": 5}}
    native = SimpleNamespace(command=commands.append, observe=lambda snapshot: snapshot)
    backend = SimpleNamespace(_factory=native, enable_factory=native_catalog)
    base = BackgroundWorkLoop if background else HierarchicalLoop
    loop_type = input_loop_type(buffered_loop_type(base))
    options = {"policy": "deterministic", "factory_scheduling": "ready-work"}
    loop_type(backend, **options)
    adapter = backend._factory
    for _ in range(25):
        loop_type(backend, **options)
        assert backend._factory is adapter
        observed = backend._factory.observe(deepcopy(state))
        if background:
            assert observed.inventory == {"coal": 5}
    assert len(commands) == (3 if background else 2)


class RouteBackend:
    input_routes_supported = True
    output_buffers_supported = True
    craft_jobs_supported = True

    def __init__(self):
        self.state = game()
        self.calls = []
        self.raise_after_build = False
        self.fault_after_build = False

    def enable_factory(self):
        return native_catalog()

    def observe(self):
        if self.calls and self.fault_after_build:
            row(self.state)["state"] = "fault"
        return deepcopy(self.state)

    def execute(self, action, args):
        self.calls.append((action, deepcopy(args)))
        if action == COMMAND:
            build(self.state, args)
            if self.raise_after_build:
                raise TimeoutError("Synthetic lost response")
        elif action != "factory_wait":
            raise AssertionError("Unexpected synthetic command")
        return "Synthetic command returned; verify observed evidence"

    def act(self, action):
        assert action == "idle"
        self.state.tick += 1
        for name in ("input_routes", "output_buffers"):
            self.state.factory[name]["tick"] = self.state.tick
        return "Synthetic idle"


RouteLoop = input_loop_type(buffered_loop_type(HierarchicalLoop))


class ScenarioLoop(RouteLoop):
    def _compile_candidates(self, snapshot):
        data = row(snapshot)
        if len(data["steps"]) != len(data["parts"]):
            p = parameters(snapshot)
            step = Step(COMMAND, "input_component", parameters=p,
                        costs=remaining(data, 20), timeout_ticks=18000)
            return [Plan("route:" + p["part"], self.memory.active_goal, "Build paid component", (step,))], ""
        step = Step("factory_wait", "input_flow", LAYOUT, 3,
                    verification={"role": SOURCE}, timeout_ticks=36000)
        return [Plan("route:commission", self.memory.active_goal, "Observe real flow evidence", (step,))], ""


def controller(backend, tmp_path, *, kind=ScenarioLoop, resume=False):
    loop = kind(backend, target="rocket_launch", policy="deterministic",
                factory_scheduling="ready-work", tick_seconds=0,
                checkpoint=str(tmp_path / "state.json"), resume_controller=resume)
    if not resume:
        loop.memory = loop.memory_type(
            backend.state.session_id, "rocket_launch", active_goal="rocket_launch",
            completed_goals={"stockpile_fuel": 0, "bootstrap_mining": 0}, last_tick=300)
    return loop


def test_paid_build_sequence_keeps_science_reserve_and_requires_flow(tmp_path):
    backend = RouteBackend()
    loop = controller(backend, tmp_path)
    for _ in range(5):
        record = loop.step()
        assert record["action"] == COMMAND and record["verified"] is True
        assert loop.memory.pending is None and loop.memory.reservations == {}
    assert backend.state.inventory["transport-belt"] == 20
    assert [p["part"] for _, p in backend.calls] == ["inserter", "belt:3", "belt:2", "belt:1", "drill"]
    assert loop.step()["verified"] is False
    assert loop.memory.pending["action"] == "factory_wait"
    assert not flow_complete(SOURCE, LAYOUT, backend.state)
    commission(backend.state)
    assert loop.step()["verified"] is True
    assert loop.memory.pending is None
    assert len(backend.calls) == 6


def test_lost_build_ack_reconciles_without_duplicate_build(tmp_path):
    backend = RouteBackend()
    backend.raise_after_build = True
    loop = controller(backend, tmp_path)
    assert loop.step()["verified"] is False
    assert loop.memory.pending["dispatch"] == "ambiguous"
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed.step()["verified"] is True
    assert len(backend.calls) == 1
    assert "inserter" in resumed.memory.input_commitments[SOURCE]["parts"]


def test_returned_build_fault_preserves_pending_and_reservations(tmp_path):
    backend = RouteBackend()
    backend.fault_after_build = True
    loop = controller(backend, tmp_path)
    result = loop.step()
    assert result["status"] == "uncertain" and not result["verified"]
    pending, reservations = deepcopy(loop.memory.pending), deepcopy(loop.memory.reservations)
    assert pending["dispatch"] == "returned" and reservations
    loop.step()
    assert loop.memory.pending == pending and loop.memory.reservations == reservations
    assert len(backend.calls) == 1


def test_missing_runtime_after_restart_does_not_drop_owned_parts(tmp_path):
    backend = RouteBackend()
    controller(backend, tmp_path).step()
    backend.state.factory["input_routes"]["sources"] = {}
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed.step()["status"] == "uncertain"
    assert SOURCE in resumed.memory.input_commitments and len(backend.calls) == 1


def test_checkpoint_write_failure_prevents_dispatch_and_poisons_instance(tmp_path, monkeypatch):
    backend = RouteBackend()
    loop = controller(backend, tmp_path)
    def fail(path):
        raise OSError("Synthetic disk failure")
    monkeypatch.setattr(loop.memory, "save", fail)
    with pytest.raises(OSError):
        loop.step()
    with pytest.raises(RuntimeError, match="persistence"):
        loop.step()
    assert backend.calls == []


def test_old_reader_rejects_named_input_extension(tmp_path):
    backend = RouteBackend()
    loop = controller(backend, tmp_path)
    loop.step()
    with pytest.raises(ValueError):
        CampaignMemory.load(tmp_path / "state.json", backend.state.session_id, "rocket_launch")


@pytest.mark.parametrize("field,value", [("paid", True), ("unit_number", -1), ("receipt", ""), ("role", [])])
def test_invalid_checkpoint_receipts_are_rejected(tmp_path, field, value):
    backend = RouteBackend()
    controller(backend, tmp_path).step()
    path = tmp_path / "state.json"
    saved = json.loads(path.read_text())
    saved["input_commitments"][SOURCE]["parts"]["inserter"][field] = value
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError):
        RouteLoop.memory_type.load(path, backend.state.session_id, "rocket_launch")


def test_model_projection_compacts_only_owned_belts(tmp_path):
    backend = RouteBackend()
    full(backend.state)
    loop = controller(backend, tmp_path)
    original = deepcopy(backend.state.for_jev())
    facts = loop._model_facts(backend.state)
    assert backend.state.for_jev() == original
    for n in (1, 2, 3):
        assert f"input:belt:{n}" not in facts["factory"]["entities"]
        assert f"input:belt:{n}" in original["factory"]["entities"]
    assert facts["factory"]["input_routes"]["sources"][SOURCE]["belt_count"] == 3
    assert facts["factory"]["input_routes"]["sources"][SOURCE]["next_component"] is None


def test_planner_reserves_remaining_kit_and_twenty_science_belts():
    state = game()
    step = InputRoutePlanner(native_catalog(), state, "rocket_launch")._need("iron-plate", 20).steps[0]
    assert step.action == COMMAND and step.parameters["part"] == "inserter"
    assert step.costs["transport-belt"] == 23 and step.parameters["reserve_belts"] == 20
    assert step.allowed(state)


def test_small_bootstrap_requirement_does_not_build_input_infrastructure():
    state = game()
    state.factory["entities"]["out:chest"]["output"]["iron-plate"] = 1
    step = InputRoutePlanner(native_catalog(), state, "iron_smelting")._need("iron-plate", 1).steps[0]
    assert step.action == "factory_extract" and step.parameters["quantity"] == 1


def test_full_route_waits_for_flow_not_manual_ore():
    state = game()
    full(state)
    planner = InputRoutePlanner(native_catalog(), state, "rocket_launch")
    step = planner._need("iron-plate", 20).steps[0]
    assert step.action == "factory_wait" and step.effect == "input_flow"
    commission(state)
    step = InputRoutePlanner(native_catalog(), state, "rocket_launch")._need("iron-plate", 20).steps[0]
    assert step.action == "factory_wait" and step.effect == "machine_output"


def test_oversized_science_reserve_is_not_silently_truncated():
    value = native_catalog()
    value.recipes["logistic-science-pack"]["ingredients"][0]["amount"] = 201
    with pytest.raises(ValueError, match="reserve"):
        InputRoutePlanner(value, game(), "rocket_launch")._science_reserve()


def test_background_memory_composes_without_losing_fields(tmp_path):
    combined = input_loop_type(buffered_loop_type(BackgroundWorkLoop))
    backend = RouteBackend()
    loop = controller(backend, tmp_path, kind=combined)
    loop._observe()
    memory = combined.memory_type.load(tmp_path / "state.json", backend.state.session_id, "rocket_launch")
    assert memory.background_schema == 2 and memory.background_job is None
    assert memory.background_attempt is None
    assert memory.input_routes_schema == 1 and memory.input_commitments == {}


def test_native_decorator_uses_prepare_walk_build_order():
    events = []
    native = SimpleNamespace()
    native.command = lambda script: events.append("install")
    native.call = lambda name, args: (events.append(name) or json.dumps({"position": {"x": 1.5, "y": 2.5}, "name": "transport-belt"}))
    native.backend = SimpleNamespace(_fair=SimpleNamespace(approach=lambda pos, name: events.append("walk")))
    adapter = InputRouteFactory(native)
    adapter.execute(COMMAND, parameters(game()))
    assert events == ["install", "prepare_input_route", "walk", "build_input_route"]


def test_cli_requires_explicit_output_buffer_flag(monkeypatch):
    from jev_factorio import main
    monkeypatch.setattr("sys.argv", ["jev-factorio", "--furnace-input-belts"])
    def reject_backend(*args, **kwargs):
        raise AssertionError("Invalid CLI must not initialize a world")
    monkeypatch.setattr(main, "make_backend", reject_backend)
    with pytest.raises(SystemExit) as error:
        main.cli()
    assert error.value.code == 2


@pytest.mark.parametrize('stage', ['route_schema', 'production_sites', 'commitment', 'live_route'])
def test_validation_failure_is_structured_and_preserves_barrier(tmp_path, monkeypatch, stage):
    import jev_factorio.input_controller as module
    backend = RouteBackend()
    loop = controller(backend, tmp_path)
    before = deepcopy(loop.memory.pending)
    secret = 'private-error-must-not-be-exported'
    if stage in {'route_schema', 'production_sites'}:
        def fail(snapshot):
            raise ValueError(secret)
        monkeypatch.setattr(module, 'sources' if stage == 'route_schema' else 'production_sites', fail)
    elif stage == 'commitment':
        loop.memory.input_commitments[SOURCE] = {'layout': 'missing', 'source_unit': 999, 'parts': {}}
    else:
        monkeypatch.setattr(module, 'current', lambda row, snapshot: False)
    loop._observe()
    details = loop._record_extras()['input_validation_failure']
    assert details['stage'] == stage and details['exception_class'] == 'ValueError'
    assert ('source' in details) == (stage in {'commitment', 'live_route'})
    assert secret not in json.dumps(details)
    assert loop.memory.status == 'uncertain' and loop._execution_barrier(backend.state)
    assert loop.memory.pending == before and backend.calls == []


def test_validation_diagnostic_rejects_unknown_source_and_keeps_first_fault(tmp_path, monkeypatch):
    import jev_factorio.input_controller as module
    backend = RouteBackend()
    loop = controller(backend, tmp_path)
    loop.memory.input_commitments['private-source-label'] = {'layout': 'missing', 'source_unit': 999, 'parts': {}}
    loop._observe()
    original = deepcopy(loop._record_extras()['input_validation_failure'])
    assert original == {'stage': 'commitment', 'exception_class': 'ValueError'}
    def fail(snapshot):
        raise TypeError('private-payload')
    monkeypatch.setattr(module, 'sources', fail)
    loop._observe()
    assert loop._record_extras()['input_validation_failure'] == original
    assert loop._execution_barrier(backend.state)


def test_route_kit_bootstraps_shared_gear_dependency_from_owned_output():
    state = game()
    state.inventory.pop('burner-inserter')
    state.factory['entities']['out:chest']['output'] = {'iron-plate': 50}
    data = native_catalog()
    data.recipes['iron-gear-wheel'] = recipe('iron-gear-wheel', {'iron-plate': 2})
    data.recipes['burner-inserter'] = recipe('burner-inserter', {'iron-gear-wheel': 1, 'iron-plate': 1})
    data.recipes['automation-science-pack'] = recipe('automation-science-pack', {'iron-gear-wheel': 1})
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    plan = planner._need('automation-science-pack', 20)
    assert plan.steps[0].action == 'factory_extract'
    assert plan.steps[0].parameters['role'] == 'out:chest'
    assert not planner._acquiring_route


@pytest.mark.parametrize('nested', [False, True])
def test_route_kit_still_rejects_real_recipe_cycles_and_restores_guard(nested):
    data = native_catalog()
    data.recipes['burner-inserter'] = recipe('burner-inserter', {'iron-gear-wheel': 1})
    data.recipes['iron-gear-wheel'] = recipe('iron-gear-wheel', {'burner-inserter': 1})
    state = game()
    state.inventory.pop('burner-inserter')
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    planner._acquiring_route = nested
    with pytest.raises(ValueError, match='Cyclic production dependency'):
        planner._acquire('burner-inserter', 1, ('item:automation-science-pack',))
    assert planner._acquiring_route is nested


def test_route_kit_retains_shared_expansion_budget():
    state = game()
    state.inventory.pop('burner-inserter')
    planner = InputRoutePlanner(native_catalog(), state, 'rocket_launch')
    planner.expansions = planner.max_expansions
    with pytest.raises(ValueError, match='expansion budget'):
        planner._acquire('burner-inserter', 1, ())
    assert not planner._acquiring_route


def test_route_kit_suppresses_optional_capital_and_restores_flags(monkeypatch):
    state = game()
    state.inventory.pop('burner-inserter')
    state.factory['entities']['out:chest']['output'] = {'iron-plate': 50}
    data = native_catalog()
    data.recipes['iron-gear-wheel'] = recipe('iron-gear-wheel', {'iron-plate': 2})
    data.recipes['burner-inserter'] = recipe('burner-inserter', {'iron-gear-wheel': 1})
    planner = InputRoutePlanner(data, state, 'rocket_launch')
    monkeypatch.setattr(planner, '_investment_machine', lambda recipe: pytest.fail('Kit must not propose nested capital'))
    monkeypatch.setattr(planner, '_workload', lambda *args: 1000)
    plan = planner._acquire('burner-inserter', 1, ('item:iron-gear-wheel',))
    assert plan.steps[0].action == 'factory_extract'
    assert plan.steps[0].parameters['role'] == 'out:chest'
    assert 'capital_investment' not in (plan.materials or {})
    assert not planner._acquiring_route and not planner._economic_acquiring
