"""Offline regression scenarios; these are not recordings of native gameplay."""
from copy import deepcopy
from types import SimpleNamespace, ModuleType
import json
import sys

import pytest

from jev_factorio.backends.fair_actions import FairActions
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.memory import CampaignMemory
from jev_factorio.planning.ready_work import ReadyWorkPlanner, compile_ready_factory
from jev_factorio.skills import Plan, Step
from jev_factorio.telemetry import make_attempt
from test_factory import catalog, machine, recipe, snapshot
from importlib.resources import files


def production_state(output=1, buffered=8, crafting=True, fuel=10):
    data = catalog()
    data.recipes["copper-plate"] = recipe("copper-plate", {"copper-ore": 1}, "smelting")
    data.recipes["automation-science-pack"] = recipe(
        "automation-science-pack", {"iron-plate": 1, "copper-plate": 1}
    )
    state = snapshot(nearby_resources={"iron-ore": 5, "copper-ore": 7, "coal": 2})
    state.factory["entities"]["recipe:iron-plate"] = machine(
        recipe="iron-plate", output={"iron-plate": output},
        input={"iron-ore": buffered}, crafting=crafting, fuel={"coal": fuel}
    )
    state.factory["entities"]["recipe:copper-plate"] = machine(
        recipe="copper-plate", fuel={"coal": 10}
    )
    return data, state


def test_twenty_plate_batch_waits_instead_of_picking_up_one_plate():
    data, state = production_state(buffered=18)
    planner = ReadyWorkPlanner(data, state, "rocket_launch")
    step = planner._need("iron-plate", 20).steps[0]
    assert step.action == "factory_wait"
    assert step.effect == "machine_output" and step.threshold == 10
    assert step.verification == {"role": "recipe:iron-plate"}
    assert not step.satisfied(state)


def test_available_collection_batch_is_taken_without_waiting():
    data, state = production_state(output=10)
    step = ReadyWorkPlanner(data, state, "rocket_launch")._need("iron-plate", 20).steps[0]
    assert step.action == "factory_extract" and step.parameters["quantity"] == 10


@pytest.mark.parametrize("amount", [1, 2, 3])
def test_small_critical_demand_is_not_delayed_by_collection_floor(amount):
    data, state = production_state(output=amount)
    step = ReadyWorkPlanner(data, state, "rocket_launch")._need("iron-plate", amount).steps[0]
    assert step.action == "factory_extract" and step.parameters["quantity"] == amount


@pytest.mark.parametrize("crafting,fuel", [(False, 10), (True, 0), (True, 4)])
def test_stopped_or_low_fuel_furnace_does_not_assume_future_output(crafting, fuel):
    data, state = production_state(output=3, buffered=0, crafting=crafting, fuel=fuel)
    step = ReadyWorkPlanner(data, state, "rocket_launch")._need("iron-plate", 20).steps[0]
    assert step.action == "factory_extract" and step.parameters["quantity"] == 3


def test_batch_target_is_capped_by_buffered_and_inflight_output():
    data, state = production_state(output=1, buffered=0)
    step = ReadyWorkPlanner(data, state, "rocket_launch")._need("iron-plate", 20).steps[0]
    assert step.action == "factory_wait" and step.threshold == 2


def test_never_waits_for_chest_to_produce_more_items():
    data, state = production_state()
    state.factory["entities"] = {"stock:chest": machine("wooden-chest", output={"iron-plate": 2})}
    step = ReadyWorkPlanner(data, state, "rocket_launch")._need("iron-plate", 20).steps[0]
    assert step.action == "factory_extract" and step.parameters["quantity"] == 2


@pytest.mark.parametrize("available", [2, 20])
def test_available_chest_stock_preempts_furnace_batch_wait(available):
    data, state = production_state()
    state.factory["entities"]["stock:chest"] = machine(
        "wooden-chest", output={"iron-plate": available}
    )
    plans, blocker = compile_ready_factory("iron_smelting", state, data)
    assert not blocker
    step = plans[0].steps[0]
    assert step.action == "factory_extract"
    assert step.parameters["role"] == "stock:chest"
    assert step.parameters["quantity"] == min(10, available)


def test_other_small_active_furnace_does_not_defeat_batch_wait():
    data, state = production_state()
    state.factory["entities"]["recipe:alternate-iron"] = machine(
        recipe="alternate-iron", output={"iron-plate": 1},
        input={"iron-ore": 8}, crafting=True, fuel={"coal": 10}
    )
    data.recipes["alternate-iron"] = data.recipes["iron-plate"]
    step = ReadyWorkPlanner(data, state, "iron_smelting")._need("iron-plate", 20).steps[0]
    assert step.action == "factory_wait"


def test_independent_copper_work_is_offered_while_iron_smelts():
    data, state = production_state()
    before = deepcopy(state)
    plans, blocker = compile_ready_factory("automation_science", state, data)
    assert not blocker
    assert plans[0].steps[0].action == "factory_gather"
    assert plans[0].steps[0].parameters == {"resource": "copper-ore", "quantity": 10}
    assert all(plan.steps[0].action != "factory_wait" for plan in plans)
    assert len({plan.id for plan in plans}) == len(plans)
    assert state == before
    assert "Next production batch: 10 automation-science-pack" in plans[0].description


def test_distinct_ready_ingredients_are_choices_not_just_one_plan():
    data, state = production_state(output=0, buffered=0, crafting=False)
    plans, _ = compile_ready_factory("automation_science", state, data)
    resources = {plan.steps[0].parameters.get("resource") for plan in plans}
    assert {"iron-ore", "copper-ore"} <= resources
    assert all(plan.steps[0].allowed(state) for plan in plans)
    assert all(Plan.from_dict(plan.to_dict()) == plan for plan in plans)


def test_shared_forecast_is_optional_and_immediate_raw_requirement_is_preserved():
    data, state = production_state(output=0, buffered=0, crafting=False)
    data.recipes["gear"] = recipe("gear", {"iron-plate": 2})
    data.recipes["automation-science-pack"] = recipe(
        "automation-science-pack", {"gear": 2, "iron-plate": 3}
    )
    plans, _ = compile_ready_factory("automation_science", state, data)
    step = plans[0].steps[0]
    assert step.action == "factory_gather"
    assert step.parameters == {"resource": "iron-ore", "quantity": 20}
    assert step.threshold == 20
    # A separate horizon candidate still amortizes optional gathering.
    assert any(p.steps[0].parameters == {"resource": "iron-ore", "quantity": 50} for p in plans)
    assert "site:" in plans[0].id  # Preserve native target/failure provenance.


def test_wood_remains_one_native_tree_interaction():
    data, state = production_state()
    state.nearby_resources["wood"] = 2
    state.factory["fair_resource_targets"]["wood"] = {
        "name": "tree-01", "surface_index": 1, "position": {"x": 2, "y": 1}
    }
    step = ReadyWorkPlanner(data, state, "rocket_launch")._need("wood", 20).steps[0]
    assert step.parameters == {"resource": "wood", "quantity": 1}


def test_missing_native_resource_identity_cannot_authorize_speculative_gather():
    data, state = production_state(output=0, buffered=0, crafting=False)
    state.factory["fair_resource_targets"].pop("copper-ore")
    plans, _ = compile_ready_factory("automation_science", state, data)
    assert not any(plan.steps[0].parameters.get("resource") == "copper-ore" for plan in plans)


def test_wait_remains_when_no_independent_work_exists():
    data, state = production_state()
    plans, blocker = compile_ready_factory("iron_smelting", state, data)
    assert not blocker and len(plans) == 1
    assert plans[0].steps[0].action == "factory_wait"


def test_queue_is_still_a_barrier_without_background_job_reconciliation():
    data, state = production_state()
    state.factory["crafting_queue"] = 1
    plans, _ = compile_ready_factory("automation_science", state, data)
    assert len(plans) == 1 and plans[0].steps[0].effect == "crafting_idle"


@pytest.mark.parametrize("batch,budget", [(0, 8), (51, 8), (True, 8), (10, 0), (10, 17), (10, True)])
def test_planner_rejects_invalid_budgets(batch, budget):
    data, state = production_state()
    with pytest.raises(ValueError):
        ReadyWorkPlanner(data, state, "iron_smelting", batch, budget)


def test_offered_candidates_obey_budget():
    data, state = production_state(output=0, buffered=0, crafting=False)
    assert len(ReadyWorkPlanner(data, state, "automation_science", max_candidates=1).candidates()) == 1


def pending_loop(state, data, scheduling="ready-work", dispatch="returned", effect="machine_output"):
    backend = SimpleNamespace(act=lambda action: None)
    loop = HierarchicalLoop(backend, target="automation_science", policy="deterministic",
                            factory_scheduling=scheduling, tick_seconds=0)
    loop.catalog = data
    loop.memory = CampaignMemory(state.session_id, "automation_science")
    loop.memory.active_goal = "automation_science"
    loop.memory.last_tick = state.tick
    if effect == "machine_output":
        step = Step("factory_wait", effect, "iron-plate", 10,
                    verification={"role": "recipe:iron-plate"})
    else:
        step = Step("factory_craft", "inventory", "automation-science-pack", 10,
                    parameters={"recipe": "automation-science-pack", "batches": 10})
    plan = Plan("pending-test", "automation_science", "Existing pending work", (step,))
    loop.memory.active_plan = plan.to_dict()
    loop.memory.pending = {"started_tick": state.tick, "polls": 0,
                           "action": step.action, "dispatch": dispatch}
    loop.memory.attempt = make_attempt(
        loop.memory.session_id, loop.target, loop.memory.active_plan,
        0, loop.memory.pending, process_id=loop._process_id,
    )
    return loop


def test_acknowledged_passive_wait_yields_without_dispatching_or_claiming_success():
    data, state = production_state()
    loop = pending_loop(state, data)
    result = loop._verify_pending(state)
    assert result["action"] == "observe" and not result["verified"]
    assert loop.memory.pending is None and loop.memory.active_plan is None
    assert any(event["kind"] == "passive_wait_yielded" for event in loop.memory.history)
    assert loop.memory.failures == {}


@pytest.mark.parametrize("dispatch", ["prepared", "ambiguous"])
def test_unacknowledged_wait_is_never_released_by_ready_work(dispatch):
    data, state = production_state()
    loop = pending_loop(state, data, dispatch=dispatch)
    loop._verify_pending(state)
    assert loop.memory.pending is not None
    assert loop.memory.pending["dispatch"] == dispatch


@pytest.mark.parametrize("dispatch", ["returned", "prepared", "ambiguous"])
def test_pending_crafting_is_not_released_even_when_other_work_exists(dispatch):
    data, state = production_state()
    loop = pending_loop(state, data, dispatch=dispatch, effect="inventory")
    loop._verify_pending(state)
    assert loop.memory.pending is not None
    assert loop.memory.active_plan["steps"][0]["action"] == "factory_craft"


def test_serial_scheduler_preserves_pending_wait_behavior():
    data, state = production_state()
    loop = pending_loop(state, data, scheduling="serial")
    loop._verify_pending(state)
    assert loop.memory.pending is not None


def test_failed_candidate_cannot_release_a_passive_wait(monkeypatch):
    data, state = production_state()
    loop = pending_loop(state, data)
    candidates, _ = loop._compile_candidates(state)
    for plan in candidates:
        loop.memory.failures[plan.id] = 2
    before = dict(loop.memory.failures)
    loop._verify_pending(state)
    assert loop.memory.pending is not None and loop.memory.failures == before


def test_uncertain_status_retains_reconciliation_barrier():
    data, state = production_state()
    loop = pending_loop(state, data)
    loop.memory.status = "uncertain"
    loop._verify_pending(state)
    assert loop.memory.pending is not None


def test_ready_fallback_preserves_compiler_priority_not_alphabetical_id():
    data, state = production_state(output=0, buffered=0, crafting=False)
    loop = pending_loop(state, data)
    plans, _ = loop._compile_candidates(state)
    assert loop._fallback_plan(list(reversed(plans))) == plans[-1]
    loop.factory_scheduling = "serial"
    assert loop._fallback_plan(plans) == min(plans, key=lambda plan: (len(plan.steps), plan.id))


@pytest.fixture
def fair_runtime():
    # Use Lupa's installed runtime, not an optional version-specific submodule.
    # Load the real control adapter so actor/speed/reach guards are not reimplemented.
    from lupa import LuaRuntime

    runtime = LuaRuntime()
    runtime.execute("""
        handlers = {}
        defines = {events = {on_tick = 1, on_script_path_request_finished = 2}}
        script = {
            get_event_handler = function(event) return handlers[event] end,
            on_event = function(event, callback) handlers[event] = callback end,
            on_nth_tick = function() end
        }
        character = {valid = true, unit_number = 9}
        reachable = true
        player = {connected = true, character = character, cheat_mode = false,
            position = {x = 0, y = 0}, surface = {},
            can_reach_entity = function() return reachable end}
        player.surface.find_entity = function() return built_entity end
        surface = player.surface
        prototypes = {entity = {}}
        game = {tick = 0, speed = 1, get_player = function() return player end}
        storage = {agent_characters = {character}}
    """)
    runtime.execute(files("jev_factorio").joinpath("lua/fair_actions.lua").read_text())
    return runtime


@pytest.fixture
def position_module(monkeypatch):
    parent, env = ModuleType("fle"), ModuleType("fle.env")
    env.Position = SimpleNamespace
    parent.env = env
    monkeypatch.setitem(sys.modules, "fle", parent)
    monkeypatch.setitem(sys.modules, "fle.env", env)


@pytest.mark.parametrize("reachable", [True, False])
def test_native_reach_controls_whether_an_approach_walks(fair_runtime, position_module, reachable):
    runtime = fair_runtime
    runtime.execute('''
        prototypes.entity["stone-furnace"] = {selection_box = {
            left_top = {x = -0.8, y = -0.8}, right_bottom = {x = 0.8, y = 0.8}
        }}
        built_entity = {valid = true, name = "stone-furnace"}
        player.reach_distance = 10
        surface.find_non_colliding_position = function() return {x = 2.3, y = 0} end
        helpers, rcon = {}, {}
    ''')
    runtime.globals().reachable = reachable
    runtime.globals().helpers.json_to_table = lambda text: runtime.table_from(json.loads(text), recursive=True)
    def encode(table):
        result = dict(table.items())
        if "positions" in result:
            result["positions"] = [dict(point.items()) for point in result["positions"].values()]
        return json.dumps(result)
    runtime.globals().helpers.table_to_json = encode
    outputs = []
    runtime.globals().rcon.print = outputs.append
    fair = FairActions.__new__(FairActions)
    moves, scripts = [], []

    def command(script):
        scripts.append(script)
        runtime.execute(script)
        return outputs[-1]

    def move(position):
        moves.append(position)
        runtime.globals().reachable = True
    fair.command, fair.move_to = command, move
    fair.approach(SimpleNamespace(x=0, y=0), "stone-furnace")
    assert len(scripts) == (1 if reachable else 2)  # Successful walking requires a fresh reach check.
    assert len(moves) == (0 if reachable else 1)
    assert fair.metrics["approach_requests"] == 1
    assert fair.metrics.get("approaches_skipped_in_reach", 0) == int(reachable)
    assert runtime.eval("player.position.x") == 0
    assert runtime.eval("game.speed") == 1


def test_reach_fast_path_still_rejects_changed_fair_actor(fair_runtime, position_module):
    fair_runtime.execute("game.speed = 2")
    fair = FairActions.__new__(FairActions)
    fair.command = fair_runtime.execute
    fair.move_to = lambda *_: pytest.fail("Must not walk after invariant failure")
    with pytest.raises(Exception, match="normal game speed"):
        fair.approach(SimpleNamespace(x=0, y=0), "stone-furnace")


def test_undepleted_ore_uses_one_native_mining_start_for_a_batch(position_module):
    fair = FairActions.__new__(FairActions)
    calls = []

    def call(name, *arguments):
        calls.append((name, arguments))
        if name == "mine_approach":
            return {"reachable": True, "identity": 71}
        return {}

    fair.call = call
    fair.wait = lambda: {"gained": 20}
    fair.move_to = lambda *_: pytest.fail("Reachable ore must not request movement")
    assert fair.harvest("iron-ore", SimpleNamespace(x=1, y=2), 20) == 20
    assert [name for name, _ in calls] == ["mine_approach", "begin_mine"]
    assert calls[-1][1][-2:] == (20, 71)
    assert fair.metrics["mining_starts"] == 1
    assert fair.metrics["mined_items"] == 20


def test_cli_rejects_ready_work_for_flat_controller_before_backend_start(monkeypatch):
    import jev_factorio.main as main
    monkeypatch.setattr(main, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr(main, "make_backend", lambda *a, **k: pytest.fail("Must reject before backend creation"))
    monkeypatch.setattr(sys, "argv", ["jev-factorio", "--factory-scheduling", "ready-work"])
    with pytest.raises(SystemExit) as error:
        main.cli()
    assert error.value.code == 2


def test_ancestor_recipe_does_not_inflate_a_small_plate_requirement():
    data, state = production_state()
    data.recipes["bulk-part"] = recipe("bulk-part", {"iron-plate": 1})
    data.recipes["bulk-part"]["products"][0]["amount"] = 20
    step = ReadyWorkPlanner(data, state, "rocket_launch")._need("bulk-part", 20).steps[0]
    assert step.action == "factory_extract"
    assert step.parameters["item"] == "iron-plate" and step.parameters["quantity"] == 1


def test_no_entity_uses_construction_approach_without_treating_it_as_reachable(position_module):
    fair = FairActions.__new__(FairActions)
    fair.command = lambda script: json.dumps({"x": 2, "y": 3})
    moves = []
    fair.move_to = moves.append
    fair.approach(SimpleNamespace(x=0, y=0), "stone-furnace")
    assert len(moves) == 1
    assert fair.metrics.get("approaches_skipped_in_reach", 0) == 0
