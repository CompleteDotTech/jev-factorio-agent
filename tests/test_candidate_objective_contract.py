"""Replay the native067 conflict between shared and qualified local objectives."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.state import GameSnapshot


def captured_frontier():
    fixtures = Path(__file__).parent / "fixtures"
    captured = json.loads((fixtures / "native067-candidate-objective-contract.json").read_text())
    catalog = Catalog.from_dict(json.loads(
        (fixtures / "native061-candidate-local-raw-demand.json").read_text())["catalog"])
    snapshot = GameSnapshot(**deepcopy(captured["snapshot"]))
    snapshot._coherent_observation_verified = (snapshot.session_id, snapshot.tick)
    snapshot._atomic_inventory_verified = (snapshot.session_id, snapshot.tick)
    plans = MiningOutpostPlanner(catalog, snapshot, "rocket_launch").candidates()
    state = deepcopy(captured["state"])
    state["candidate_evidence"] = candidate_evidence(snapshot, catalog, plans)
    return captured, plans, state


def test_native067_contract_and_rubric_resolve_candidate_local_objective():
    captured, plans, state = captured_frontier()
    before = deepcopy(state)
    packet, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert [p.steps[0].threshold for p in offered] == [13, 20, 40]
    assert [p.id for p in offered] == list(captured["state"]["candidate_plans"])
    manual = offered[1]
    old_contract = captured["state"]["execution_contract"]
    assert "Judge the supplied local_objective when present" in old_contract
    assert "supplied objective" in captured["questions"][manual.id + "/useful_progress"]["criteria"]["useful"]
    assert "For qualified candidate-local raw demand, judge that candidate's evidence row local_target" in packet["execution_contract"]
    assert "otherwise judge supplied local_objective or active_goal" in packet["execution_contract"]
    useful = questions[manual.id + "/useful_progress"]
    assert "this candidate's evidence row local_target" in useful["criteria"]["useful"]
    assert "candidate_plans[" in useful["instructions"] and "candidate_evidence[" in useful["instructions"]
    assert packet["candidate_evidence"][manual.id]["local_target"]["item"] == "automation-science-pack"
    for field in ("facts", "history", "local_objective", "candidate_evidence"):
        assert packet[field] == captured["state"][field]
    for plan in offered:
        recorded = captured["state"]["candidate_plans"][plan.id]
        assert json.loads(json.dumps(packet["candidate_plans"][plan.id]["steps"])) == recorded["steps"]
        assert all(plan.materials[key] == value for key, value in recorded["materials"].items())
    for other in (offered[0], offered[2]):
        assert questions[other.id + "/useful_progress"] == captured["questions"][other.id + "/useful_progress"]
    assert questions[manual.id + "/benefit"] == captured["questions"][manual.id + "/benefit"]
    assert state == before
    assert len(json.dumps({"state": packet, "questions": questions}, ensure_ascii=False, allow_nan=False).encode()) < 48000


@pytest.mark.parametrize("field,value", [
    ("tick", -1), ("session_id", "other"), ("catalog_version", "2.0.76"),
    ("schema", True), ("parent_source_unit", []),
])
def test_unqualified_raw_demand_keeps_shared_contract_and_rubric(field, value):
    captured, plans, state = captured_frontier()
    manual = plans[1]
    # Same current native facts; only the qualification's identity is stale.
    state["candidate_evidence"][manual.id]["candidate_local_raw_demand"][field] = value
    packet, questions, offered = question_batch(state, plans, max_bytes=100000)
    assert [p.id for p in offered] == [p.id for p in plans]
    assert packet["execution_contract"] == captured["state"]["execution_contract"]
    assert questions[manual.id + "/useful_progress"]["criteria"]["useful"] == (
        "Current evidence supports useful progress toward the supplied objective")
    assert "to judge progress toward that row's local_target" not in questions[manual.id + "/useful_progress"]["instructions"]


def test_native067_recorded_rejections_are_not_reinterpreted():
    captured, plans, state = captured_frontier()
    answers = deepcopy(captured["answers"])
    decision = select_plan(SimpleNamespace(evaluate=lambda context, questions: deepcopy(answers)),
                           state, plans, max_bytes=48000)
    assert decision.plan_id is None
    assert decision.reason == "Candidate evidence insufficient"
    assert decision.answers == captured["answers"] == answers
