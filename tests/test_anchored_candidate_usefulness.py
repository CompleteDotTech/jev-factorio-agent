"""A judgment must identify its plan without a question-ID token branch."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.decision_support import candidate_evidence
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.state import GameSnapshot


def captured_frontier():
    fixtures = Path(__file__).parent / "fixtures"
    captured = json.loads((fixtures / "native064-anchored-usefulness.json").read_text())
    catalog_capture = json.loads((fixtures / "native061-candidate-local-raw-demand.json").read_text())
    snapshot = GameSnapshot(**deepcopy(captured["snapshot"]))
    identity = (snapshot.session_id, snapshot.tick)
    snapshot._coherent_observation_verified = identity
    snapshot._atomic_inventory_verified = identity
    catalog = Catalog.from_dict(catalog_capture["catalog"])
    plans = MiningOutpostPlanner(catalog, snapshot, "rocket_launch").candidates()
    state = deepcopy(captured["state"])
    state["candidate_evidence"] = candidate_evidence(snapshot, catalog, plans)
    return captured, snapshot, plans, state


def indexed_id(instructions, field):
    """Resolve the model-visible reference, without using a question key."""
    suffix = instructions.split("`" + field + "[", 1)[1]
    value, end = json.JSONDecoder().raw_decode(suffix)
    assert suffix[end:].startswith("]`")
    return value


def test_native064_usefulness_branch_identifies_plan_and_evidence_without_question_id():
    captured, snapshot, plans, state = captured_frontier()
    before = deepcopy(snapshot.__dict__)
    packet, questions, offered = question_batch(state, plans, max_bytes=48000)
    assert [plan.steps[0].threshold for plan in offered] == [13, 20, 40]
    assert [plan.id for plan in offered] == list(captured["state"]["candidate_plans"])
    assert packet["facts"] == captured["state"]["facts"]
    assert packet["history"] == captured["state"]["history"]
    assert packet["local_objective"] == captured["state"]["local_objective"]
    assert snapshot.__dict__ == before
    manual = offered[1]
    old = captured["questions"][manual.id + "/useful_progress"]["instructions"]
    assert "`candidate_plans[" not in old and "`candidate_evidence[" not in old
    question = questions[manual.id + "/useful_progress"]
    # The model-visible branch carries type/instructions/criteria, not its key.
    renamed = {"unrelated-question-id": deepcopy(question)}
    branch = next(iter(renamed.values()))
    plan_id = indexed_id(branch["instructions"], "candidate_plans")
    evidence_id = indexed_id(branch["instructions"], "candidate_evidence")
    assert plan_id == evidence_id == manual.id
    assert packet["candidate_plans"][plan_id]["steps"][0]["parameters"]["quantity"] == 20
    assert packet["candidate_evidence"][evidence_id]["local_target"]["item"] == "automation-science-pack"
    assert packet["candidate_evidence"][evidence_id]["candidate_local_raw_demand"]["tick"] == snapshot.tick
    assert branch["criteria"]["useful"] == (
        "Current evidence supports progress toward this candidate's evidence row local_target")
    assert branch["criteria"]["unsupported"] == captured["questions"][manual.id + "/useful_progress"]["criteria"]["unsupported"]
    assert len(json.dumps({"state": packet, "questions": questions},
                          ensure_ascii=False, allow_nan=False).encode()) < 48000
    for other in (offered[0], offered[2]):
        assert questions[other.id + "/useful_progress"] == captured["questions"][other.id + "/useful_progress"]
    assert questions[manual.id + "/benefit"] == captured["questions"][manual.id + "/benefit"]


def test_native064_recorded_rejections_remain_rejections():
    captured, snapshot, plans, state = captured_frontier()
    answers = deepcopy(captured["answers"])
    client = SimpleNamespace(evaluate=lambda context, questions: deepcopy(answers))
    decision = select_plan(client, state, plans, max_bytes=48000)
    assert decision.plan_id is None
    assert decision.reason == "Candidate evidence insufficient"
    assert decision.answers == answers == captured["answers"]
