"""Actual controller, write-ahead and planner composition with a deterministic backend."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_factorio import solid_routes as r
from jev_factorio.backends.solid_routes import SolidRouteFactory, validate_intents
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.memory import CampaignMemory, load_checkpoint
from jev_factorio.skills import Plan, Step
from jev_factorio.solid_controller import solid_loop_type
from jev_factorio.planning.solid_routes import candidates
from solid_routes_fixtures import ROUTE, INTENTS, SOURCE, TARGET, fixture, row, build, full
from test_factory import catalog, recipe


def native_catalog():
    result = catalog()
    result.recipes.update({"iron-gear-wheel": recipe("iron-gear-wheel", {"iron-plate": 2}),
                           "automation-science-pack": recipe("automation-science-pack", {"iron-gear-wheel": 1, "copper-plate": 1})})
    result.machines["assembling-machine-1"] = {"categories": {"crafting": True}, "burner": False, "electric": True, "speed": 1}
    return result


class Backend:
    solid_routes_supported = True

    def __init__(self):
        self.state = fixture()
        self.calls = []
        self.observations = 0
        self.lost_ack = False
        self.prepared_once = False
        self.ambiguous_placement = False
        self.post_dispatch = lambda: None
        self.before_observe = lambda: None
        self.checkpoint = None

    def enable_factory(self): return native_catalog()

    def observe(self):
        self.observations += 1
        self.before_observe()
        return deepcopy(self.state)

    def execute(self, action, p):
        assert action == r.COMMAND
        self.calls.append((action, deepcopy(p)))
        saved = json.loads(self.checkpoint.read_text())
        assert saved["pending"]["action"] == action and saved["pending"]["dispatch"] in {"prepared", "ambiguous"}
        assert saved["active_plan"]["steps"][0]["parameters"] == p
        if self.prepared_once:
            self.prepared_once = False
            row(self.state).update(state="building", pending={"part": p["part"], "receipt": p["receipt"], "phase": "prepared"})
            raise TimeoutError("Fixture response lost after native prepare, before actor call")
        if self.ambiguous_placement:
            row(self.state).update(state="fault", pending={"part": p["part"], "receipt": p["receipt"], "phase": "dispatching"})
            raise TimeoutError("Fixture ambiguous actor boundary")
        build(self.state, p)
        self.post_dispatch()
        if self.lost_ack:
            raise TimeoutError("Fixture lost response after paid native receipt")
        return "fixture paid component"

    def act(self, action):
        assert action == "idle"
        return "fixture idle"


class FoundationScenario(HierarchicalLoop):
    def _compile_candidates(self, snapshot):
        return [], "Fixture asks only for the new foundation capability"


Loop = solid_loop_type(FoundationScenario)


def controller(backend, path, *, resume=False, kind=Loop):
    backend.checkpoint = path / "solid-checkpoint.json"
    loop = kind(backend, target="rocket_launch", policy="deterministic", factory_scheduling="ready-work",
                tick_seconds=0, checkpoint=str(backend.checkpoint), resume_controller=resume, solid_intents=INTENTS)
    if not resume:
        loop.memory = loop.memory_type(backend.state.session_id, "rocket_launch", active_goal="iron_smelting",
                         completed_goals={"stockpile_fuel": 0, "bootstrap_mining": 0}, last_tick=backend.state.tick)
    return loop


def test_composed_paid_sequence_and_checkpoint_resume(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    for i in range(4):
        record = loop.step()
        assert record["action"] == r.COMMAND and record["verified"], record
        assert backend.observations == (i+1)*3
        assert len(loop.memory.solid_commitments[ROUTE]["parts"]) == i+1
        assert loop.memory.pending is None
    assert backend.state.inventory["inserter"] == 0 and backend.state.inventory["transport-belt"] == 0
    assert not r.flow_complete(ROUTE, row(backend.state)["layout"], backend.state)
    resumed = controller(backend, tmp_path, resume=True)
    resumed.step()
    assert len(backend.calls) == 4
    restored = load_checkpoint(backend.checkpoint, backend.state.session_id, "rocket_launch")
    assert restored.solid_commitments == loop.memory.solid_commitments
    with pytest.raises(ValueError): CampaignMemory.load(backend.checkpoint, backend.state.session_id, "rocket_launch")


def test_lost_ack_verifies_existing_receipt_without_rebuild(tmp_path):
    backend = Backend(); backend.lost_ack = True
    loop = controller(backend, tmp_path)
    assert not loop.step()["verified"] and loop.memory.pending["dispatch"] == "ambiguous"
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed.step()["verified"] and len(backend.calls) == 1


def test_native_prepared_journal_allows_one_exact_idempotent_resume(tmp_path):
    backend = Backend(); backend.prepared_once = True
    loop = controller(backend, tmp_path)
    assert not loop.step()["verified"] and not row(backend.state)["parts"]
    attempt_id = loop.memory.attempt["id"]
    resumed = controller(backend, tmp_path, resume=True)
    record = resumed.step()
    assert record["verified"] and len(backend.calls) == 2, record
    assert backend.calls[0] == backend.calls[1]
    assert resumed.memory.attempt_outcomes[-1]["id"] == attempt_id
    assert len(row(backend.state)["parts"]) == 1
    assert backend.state.inventory["inserter"] == 1


def test_native_prepared_replay_is_bounded_if_prepare_keeps_failing(tmp_path):
    backend = Backend(); backend.prepared_once = True
    loop = controller(backend, tmp_path); loop.step()
    backend.prepared_once = True
    assert not loop.step()["verified"]
    assert len(backend.calls) == 2 and loop.memory.pending["polls"] == 1
    loop.step()
    assert len(backend.calls) == 2 and loop.memory.pending is not None


def test_ambiguous_native_dispatch_never_replays_or_loses_history(tmp_path):
    backend = Backend(); backend.ambiguous_placement = True
    loop = controller(backend, tmp_path); loop.memory.failures["earlier-project"] = 2
    loop.step(); saved_pending = deepcopy(loop.memory.pending)
    resumed = controller(backend, tmp_path, resume=True)
    record = resumed.step()
    assert record["status"] == "uncertain" and len(backend.calls) == 1
    assert resumed.memory.pending == saved_pending and resumed.memory.failures["earlier-project"] == 2


def test_precondition_observation_rejects_missing_kit_before_dispatch(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    def change():
        if backend.observations == 2: backend.state.inventory["inserter"] = 0
    backend.before_observe = change
    record = loop.step()
    assert not record["verified"] and backend.calls == []
    assert next(iter(loop.memory.failures.values())) == 1


def test_lost_checkpoint_after_paid_placement_reconciles_native_receipt(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path)
    def fault():
        loop.memory.save = lambda path: (_ for _ in ()).throw(OSError("fixture sync failure"))
    backend.post_dispatch = fault
    with pytest.raises(RuntimeError, match="persistence") as caught:
        loop.step()
    assert isinstance(caught.value.__context__, OSError)
    with pytest.raises(RuntimeError, match="persistence"): loop.step()
    backend.post_dispatch = lambda: None
    resumed = controller(backend, tmp_path, resume=True)
    assert resumed.step()["verified"] and len(backend.calls) == 1


def test_committed_kit_survives_plan_clear_and_filters_unrelated_spending(tmp_path):
    backend = Backend(); loop = controller(backend, tmp_path); loop.step()
    assert loop.memory.active_plan is None and loop.memory.reservations == {}
    p = {"role": TARGET, "item": "inserter", "quantity": 1, "receipt": "spend-kit"}
    spend = Step("factory_insert", "transfer", parameters=p, costs={"inserter": 1})
    assert not loop._step_allowed(spend, backend.state)
    assert loop.memory.solid_commitments[ROUTE]


@pytest.mark.parametrize("change", ["missing", "epoch", "prefix", "recipe", "alias"])
def test_owned_state_mismatch_stops_without_erasing_commitments(tmp_path, change):
    backend = Backend(); loop = controller(backend, tmp_path); loop.step()
    saved = deepcopy(loop.memory.solid_commitments)
    if change == "missing": backend.state.factory["solid_routes"]["routes"] = {}
    elif change == "epoch": backend.state.factory["solid_routes"]["actor_index"] = 2
    elif change == "prefix": row(backend.state)["parts"]["receive"]["receipt"] = "different"
    elif change == "recipe": backend.state.factory["entities"][SOURCE]["recipe"] = "different"
    else: backend.state.factory["entities"]["alias"] = deepcopy(backend.state.factory["entities"][SOURCE])
    record = loop.step()
    assert record["status"] == "uncertain" and len(backend.calls) == 1
    assert loop.memory.solid_commitments == saved


def test_intent_migration_and_disabled_capability_are_rejected(tmp_path):
    backend = Backend(); controller(backend, tmp_path).step()
    options = dict(checkpoint=str(backend.checkpoint), policy="deterministic", target="rocket_launch",
                   factory_scheduling="ready-work", resume_controller=True)
    changed = deepcopy(INTENTS); changed[0]["item"] = "copper-plate"
    with pytest.raises(ValueError, match="silently"):
        Loop(backend, solid_intents=changed, **options)
    ordinary = HierarchicalLoop(backend, **options)
    with pytest.raises(ValueError, match="explicit controller"):
        ordinary.step()


def test_existing_ready_science_is_retained_by_real_composed_planner(tmp_path):
    from test_maintenance_progress import progress_scenario
    from test_input_route_integration import RouteLoop
    backend, data = progress_scenario()
    extra = fixture()
    backend.state.factory["solid_routes"] = deepcopy(extra.factory["solid_routes"])
    backend.state.factory["entities"].update(extra.factory["entities"])
    backend.state.factory["solid_routes"]["session_id"] = backend.state.session_id
    backend.state.factory["solid_routes"]["tick"] = backend.state.tick
    backend.state.inventory.update(inserter=2, **{"transport-belt": 22})
    backend.solid_routes_supported = True
    loop = controller(backend, tmp_path, kind=solid_loop_type(RouteLoop))
    loop.memory.active_goal = "rocket_launch"
    plans, blocker = loop._compile_candidates(backend.state)
    assert any(p.steps[0].action == "factory_insert" and p.steps[0].parameters["role"] == "utility:lab" for p in plans), blocker
    assert any(p.steps[0].action == r.COMMAND for p in plans), blocker


def test_adapter_uses_ordered_prepare_approach_build_then_observation():
    calls = []
    native = SimpleNamespace(command=lambda s: calls.append("install"),
        call=lambda action, p: calls.append(action) or json.dumps({"position": {"x": 5.5,"y": .5}, "name": "inserter"}),
        backend=SimpleNamespace(_fair=SimpleNamespace(approach=lambda *a: calls.append("approach"))))
    adapter = SolidRouteFactory(native, INTENTS)
    p = candidates(fixture(), "rocket_launch")[0].steps[0].parameters
    message = adapter.execute(r.COMMAND, p)
    assert calls == ["install", "set_solid_intents", "prepare_solid_route", "approach", "build_solid_route"]
    assert "require observation" in message


@pytest.mark.parametrize("intents", [[], [{}], INTENTS*2, [{**INTENTS[0],"destination":"fuel"}], [{**INTENTS[0],"extra":True}]])
def test_bad_intents(intents):
    with pytest.raises(ValueError): validate_intents(intents)


def test_existing_untracked_native_route_is_not_silently_adopted(tmp_path):
    backend = Backend(); build(backend.state)
    loop = controller(backend, tmp_path)
    record = loop.step()
    assert record['status'] == 'uncertain'
    assert loop.memory.solid_commitments == {} and backend.calls == []


def test_inner_observer_checkpoint_has_binding_before_outer_observer_returns(tmp_path):
    class EarlySave(FoundationScenario):
        def _observe(self, stage='observe'):
            snapshot = super()._observe(stage)
            self._save()
            return snapshot
    kind = solid_loop_type(EarlySave)
    backend = Backend(); loop = controller(backend, tmp_path, kind=kind)
    loop.memory.active_goal = "rocket_launch"
    original = loop.memory.save
    def interrupted(path):
        original(path)
        raise OSError('fixture interrupt after first durable observation')
    loop.memory.save = interrupted
    with pytest.raises(OSError):
        loop._observe()
    saved = json.loads(backend.checkpoint.read_text())
    assert saved['solid_intents'] == INTENTS
    assert saved['solid_epoch'] == {'actor_index':1, 'surface_index':1, 'force_index':1}
    restored = kind.memory_type.load(backend.checkpoint, backend.state.session_id, 'rocket_launch')
    assert restored.pending is None and restored.solid_commitments == {}
    assert not backend.calls


@pytest.mark.parametrize('enabled,scheduling', [(False,'ready-work'), (True,'serial')])
def test_research_manifest_cannot_mislabel_solid_treatment(tmp_path, enabled, scheduling):
    from jev_factorio.research_log import ResearchLog, RunConfiguration
    cfg = RunConfiguration(backend='mock',controller='hierarchical',policy='deterministic',
                           solid_routes=enabled,factory_scheduling=scheduling)
    backend = Backend()
    with ResearchLog(tmp_path/'run', cfg, environ={}) as sink:
        with pytest.raises(ValueError, match='manifest'):
            Loop(backend, solid_intents=INTENTS, factory_scheduling='ready-work', policy='deterministic',
                 checkpoint=str(tmp_path/'checkpoint'), research_log=sink)
    assert not backend.calls and not (tmp_path/'checkpoint').exists()


def test_solid_actions_have_a_complete_research_chain_with_explicit_treatment(tmp_path):
    from jev_factorio.research_log import ResearchLog, RunConfiguration, verify_run
    cfg = RunConfiguration(backend='mock',controller='hierarchical',policy='deterministic',
                           target='rocket_launch',solid_routes=True,factory_scheduling='ready-work',
                           checkpoint_enabled=True)
    backend = Backend(); backend.checkpoint = tmp_path/'checkpoint'
    with ResearchLog(tmp_path/'run',cfg,environ={}) as sink:
        loop = Loop(backend, solid_intents=INTENTS, factory_scheduling='ready-work', policy='deterministic',
                    tick_seconds=0, checkpoint=str(backend.checkpoint), research_log=sink)
        loop.memory = loop.memory_type(backend.state.session_id,'rocket_launch',active_goal='rocket_launch',
            completed_goals={'stockpile_fuel':0,'bootstrap_mining':0},last_tick=backend.state.tick)
        for _ in range(4):
            assert loop.step()['verified']
    verified = verify_run(tmp_path/'run')
    assert verified['complete']
    manifest = json.loads((tmp_path/'run/manifest.json').read_text())
    assert manifest['configuration']['solid_routes'] is True
    events = [json.loads(line) for line in (tmp_path/'run/events.jsonl').read_text().splitlines()]
    actions = [e for e in events if e['event_type']=='action_prepared']
    assert len(actions)==4 and len(backend.calls)==4
    assert all(e['payload']['action']==r.COMMAND for e in actions)
    from jev_factorio.research_evaluation import evaluate_run
    result = evaluate_run(tmp_path/'run').summary
    assert result['evidence_class']=='synthetic' and not result['mixed_treatments']
