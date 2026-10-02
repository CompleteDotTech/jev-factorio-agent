from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_factorio.state import GameSnapshot
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.judgments import question_batch, select_plan, _qualified_candidate_local_raw_demand


def setup():
    capture = json.loads((Path(__file__).parent / "fixtures/native061-candidate-local-raw-demand.json").read_text())
    snapshot = GameSnapshot(**deepcopy(capture["snapshot"]))
    snapshot._coherent_observation_verified = (snapshot.session_id, snapshot.tick)
    snapshot._atomic_inventory_verified = (snapshot.session_id, snapshot.tick)
    catalog = Catalog.from_dict(capture["catalog"])
    native_before = deepcopy({key: getattr(snapshot, key) for key in capture["snapshot"]})
    plans = MiningOutpostPlanner(catalog, snapshot, "rocket_launch").candidates()
    rows = candidate_evidence(snapshot, catalog, plans)
    assert {key: getattr(snapshot, key) for key in capture["snapshot"]} == native_before
    state = deepcopy(capture["state"])
    state["candidate_evidence"] = rows
    return capture, snapshot, catalog, plans, rows, state


def test_actual_frontier_has_separate_native_parent_context_without_changing_objective_or_answers():
    capture, snapshot, catalog, plans, rows, state = setup()
    assert [plan.steps[0].threshold for plan in plans] == [13, 20, 40]
    for plan in plans:
        recorded = capture["state"]["candidate_plans"][plan.id]
        assert json.loads(json.dumps(plan.to_dict()["steps"])) == recorded["steps"]
        assert all(plan.materials[key] == value for key, value in recorded["materials"].items())
    before = deepcopy(snapshot.__dict__)
    packet, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert offered == plans
    assert len(json.dumps({"state":packet,"questions":questions}, ensure_ascii=False, allow_nan=False).encode()) < 48000
    assert packet["local_objective"] == capture["state"]["local_objective"]
    assert packet["facts"] == capture["state"]["facts"]
    assert packet["history"] == capture["state"]["history"]
    assert snapshot.__dict__ == before
    kit, manual, lookahead = plans
    assert "candidate_local_raw_demand" not in rows[kit.id]
    assert "candidate_local_raw_demand" not in rows[lookahead.id]
    assert rows[manual.id]["candidate_local_raw_demand"]["parent_source_unit"] == 2547
    for suffix in ("useful_progress", "benefit"):
        assert "local_target" in questions[manual.id + "/" + suffix]["instructions"]
    assert "recipe input" in questions[manual.id + "/benefit"]["instructions"]
    assert "science output" in questions[manual.id + "/useful_progress"]["instructions"]
    assert "qualified candidate-local" in questions["candidate"]["instructions"]
    assert "candidate's `local_target`" not in questions[kit.id + "/useful_progress"]["instructions"]
    client = SimpleNamespace(evaluate=lambda context, batch: deepcopy(capture["answers"]))
    decision = select_plan(client, state, plans, max_bytes=48000)
    assert decision.plan_id is None and decision.reason == "Candidate evidence insufficient"
    assert decision.answers == capture["answers"]


@pytest.mark.parametrize("change", ["coherent", "atomic", "version", "world", "parent_tick", "parent_source", "local", "path", "bill", "quantity", "scope", "recipe"])
def test_producer_refuses_stale_or_forged_current_demand(change):
    capture, snapshot, catalog, plans, rows, state = setup()
    kit, manual, lookahead = plans
    if change == "coherent": snapshot._coherent_observation_verified = None
    elif change == "atomic": snapshot._atomic_inventory_verified = None
    elif change == "version": catalog = replace(catalog, version="2.0.76")
    elif change == "world": snapshot.world_kind = "other"
    elif change == "parent_tick": kit.materials["input_route_kit_prerequisite"]["observed_tick"] -= 1
    elif change == "parent_source": kit.materials["input_route_kit_prerequisite"]["source_unit"] += 1
    elif change == "local": manual.materials["local_objective"]["item"] = "rocket-part"
    elif change == "path": manual.materials["raw_prerequisite"]["planner_item_path"][1] = "transport-belt"
    elif change == "bill": manual.materials["shortages"]["iron-ore"] += 1
    elif change == "quantity": plans[1] = replace(manual, steps=(replace(manual.steps[0], threshold=21),))
    elif change == "scope": manual.materials["work_intent"]["scope"] = "lookahead"
    elif change == "recipe": manual.materials["raw_prerequisite"]["recipe"] = "copper-plate"
    new_rows = candidate_evidence(snapshot, catalog, plans)
    assert "candidate_local_raw_demand" not in new_rows[manual.id]


@pytest.mark.parametrize("change", ["schema", "tick", "session", "version", "parent_unit", "parent_row", "local_item", "local_count", "parent_missing", "parent_duplicate", "start_tick", "start_missing", "start_stock", "action", "quantity", "path", "scope", "unknown", "reason"])
def test_consumer_does_not_redirect_invalid_evidence(change):
    capture, snapshot, catalog, plans, rows, state = setup()
    kit, manual, lookahead = plans
    row = rows[manual.id]
    proof = row["candidate_local_raw_demand"]
    if change == "schema": proof["schema"] = True
    elif change == "tick": proof["tick"] -= 1
    elif change == "session": proof["session_id"] = "other"
    elif change == "version": proof["catalog_version"] = "2.0.76"
    elif change == "parent_unit": proof["parent_source_unit"] = []
    elif change == "parent_row": rows[kit.id] = []
    elif change == "local_item": row["local_target"]["item"] = {}
    elif change == "local_count": row["local_target"]["inventory_target"] = True
    elif change == "parent_missing": rows[kit.id].pop("input_route_kit_parent_purpose")
    elif change == "parent_duplicate": rows["duplicate"] = deepcopy(rows[kit.id])
    elif change == "start_tick": row["gather_start_evidence"]["observed_tick"] -= 1
    elif change == "start_missing": row["gather_start_evidence"] = None
    elif change == "start_stock": row["gather_start_evidence"]["resource_inventory_now"] = 1
    elif change == "action": plans[1] = replace(manual, steps=(replace(manual.steps[0], action="idle"),))
    elif change == "quantity": plans[1] = replace(manual, steps=(replace(manual.steps[0], parameters={"resource":"iron-ore","quantity":19}),))
    elif change == "path": row["raw_prerequisite"]["planner_item_path"][0] = "rocket-part"
    elif change == "scope": row["work_scope"] = "lookahead"
    elif change == "unknown": row["unknowns"] = ["travel"]
    elif change == "reason": row["reasons"] = ["investment"]
    if change == "parent_row":
        assert not _qualified_candidate_local_raw_demand(manual, state["facts"], row, rows)
        return
    _, questions, _ = question_batch(state, plans, max_bytes=100000)
    assert "candidate's `local_target`" not in questions[manual.id + "/useful_progress"]["instructions"]
    assert "recompiled parent demand" not in questions[manual.id + "/benefit"]["instructions"]
