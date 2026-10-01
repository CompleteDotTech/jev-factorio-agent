"""Usefulness admission is independent of uncertainty about positive magnitude."""
import pytest

from jev_factorio.backends.mock import MockBackend
from jev_factorio.jev_client import MockJevClient
from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.skills import Plan, Step, compile_plans


class SplitBenefitModel(MockJevClient):
    def __init__(self, choice="useful", confidence=0.91, omit=False):
        self.useful_choice = choice
        self.useful_confidence = confidence
        self.omit = omit

    def evaluate(self, state, questions):
        assert state["judgment_contract"]["schema"] == 2
        answers = super().evaluate(state, questions)
        for key in list(answers):
            if key.endswith("/benefit"):
                # Sanitized distribution from the captured rejected decision.
                answers[key].update(score=1.58, confidence=0.36,
                                    probabilities={"0": 0.01, "1": 0.40, "2": 0.59})
            elif key.endswith("/useful_progress"):
                if self.omit:
                    del answers[key]
                else:
                    answers[key].update(
                        choice=self.useful_choice,
                        confidence=self.useful_confidence,
                        probabilities={"useful": float(self.useful_choice == "useful"),
                                       "unsupported": float(self.useful_choice == "unsupported")})
        return answers


def decision(client, floor=0.45):
    snapshot = MockBackend().observe()
    plans, _ = compile_plans("stockpile_fuel", snapshot)
    return select_plan(client, {"facts": snapshot.for_jev(),
                               "active_goal": "stockpile_fuel"}, plans,
                       confidence_floor=floor)


def test_positive_magnitude_split_requires_independent_usefulness():
    selected = decision(SplitBenefitModel())
    assert selected.plan_id is not None
    gate = selected.diagnostics["usefulness_gate"][selected.plan_id]
    assert gate == {"choice": "useful", "confidence": 0.91,
                    "floor": 0.45, "passed": True}
    assert selected.answers[selected.plan_id + "/benefit"]["confidence"] == 0.36


@pytest.mark.parametrize(("choice", "confidence", "reason"), [
    ("unsupported", 0.99, "no_demonstrated_progress"),
    ("useful", 0.44, "low_usefulness_confidence"),
])
def test_positive_benefit_support_cannot_authorize_missing_usefulness(choice, confidence, reason):
    rejected = decision(SplitBenefitModel(choice, confidence))
    assert rejected.plan_id is None
    assert rejected.diagnostics["outcome"] == "all_candidates_rejected"
    assert all(reason in reasons for reasons in
               rejected.diagnostics["candidate_rejections"].values())


def test_missing_independent_judgment_fails_closed():
    rejected = decision(SplitBenefitModel(omit=True))
    assert rejected.plan_id is None
    assert rejected.diagnostics["outcome"] == "invalid_answer"


@pytest.mark.parametrize("fault", ["missing-confidence", "nan", "wrong-choice", "bad-mass"])
def test_malformed_usefulness_answer_cannot_admit_a_plan(fault):
    class Malformed(SplitBenefitModel):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for key in answers:
                if key.endswith("/useful_progress"):
                    if fault == "missing-confidence":
                        answers[key].pop("confidence")
                    elif fault == "nan":
                        answers[key]["confidence"] = float("nan")
                    elif fault == "wrong-choice":
                        answers[key]["choice"] = "unsupported"
                    else:
                        answers[key]["probabilities"]["useful"] = 0.8
            return answers

    rejected = decision(Malformed())
    assert rejected.plan_id is None
    assert rejected.diagnostics["outcome"] == "invalid_answer"


def test_higher_floor_applies_to_explicit_usefulness():
    assert decision(SplitBenefitModel(confidence=0.7)).plan_id is not None
    rejected = decision(SplitBenefitModel(confidence=0.7), floor=0.75)
    assert rejected.plan_id is None
    assert all("low_usefulness_confidence" in reasons for reasons in
               rejected.diagnostics["candidate_rejections"].values())


def test_native_start_uncertainty_still_rejects_useful_positive_plan():
    class MissingStart(SplitBenefitModel):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for key in answers:
                if key.endswith("/needs_observation"):
                    answers[key]["noul"] = 0.9
            return answers

    rejected = decision(MissingStart())
    assert rejected.plan_id is None
    assert all("missing_start_evidence" in reasons for reasons in
               rejected.diagnostics["candidate_rejections"].values())


def test_request_compaction_preserves_each_plans_complete_material_evidence():
    shared = {"native_site": {"unit": 17, "description": "x" * 15000}}
    plans = [Plan(str(index), "stockpile_fuel", "gather coal",
                  (Step("mine_coal", "inventory", "coal", 5),),
                  materials={"proof": shared, "unique": {"index": index}})
             for index in range(2)]
    originals = [plan.to_dict() for plan in plans]
    context, _, offered = question_batch({}, plans, max_bytes=24000)
    assert offered == plans
    assert context["shared_plan_materials"] == {"proof": shared}
    for plan, original in zip(plans, originals):
        document = context["candidate_plans"][plan.id]
        restored = {**document["materials"], **{
            key: context["shared_plan_materials"][key]
            for key in document["shared_materials_keys"]}}
        assert restored == original["materials"]
        assert plan.to_dict() == original


def test_distinct_material_proofs_are_never_factored_together():
    plans = [Plan(str(index), "stockpile_fuel", "gather coal",
                  (Step("mine_coal", "inventory", "coal", 5),),
                  materials={"proof": {"unit": index, "description": "x" * 300}})
             for index in range(2)]
    context, _, offered = question_batch({}, plans)
    assert offered == plans
    assert "shared_plan_materials" not in context
    assert context["candidate_plans"] == {plan.id: plan.to_dict() for plan in plans}


def test_prepared_batch_is_the_exact_provider_payload():
    snapshot = MockBackend().observe()
    plans, _ = compile_plans("stockpile_fuel", snapshot)
    state = {"facts": snapshot.for_jev(), "active_goal": "stockpile_fuel"}
    prepared = question_batch(state, plans)

    class CapturedPayload(MockJevClient):
        def evaluate(self, context, questions):
            assert context is prepared[0] and questions is prepared[1]
            return super().evaluate(context, questions)

    assert select_plan(CapturedPayload(), state, plans, prepared_batch=prepared).plan_id
    with pytest.raises(ValueError, match="oversized"):
        select_plan(CapturedPayload(), state, plans, max_bytes=1, prepared_batch=prepared)


@pytest.mark.parametrize("tamper", ["description", "shared", "question", "state"])
def test_prepared_batch_tampering_fails_before_provider_call(tamper):
    from copy import deepcopy
    snapshot = MockBackend().observe()
    plans = [Plan(id=f"p{index}", goal="stockpile_fuel", description="candidate",
                  steps=(Step("mine_coal", "inventory", "coal", 5),),
                  materials={"large_proof": {"description": "x" * 1000}})
             for index in range(2)]
    state = {"facts": snapshot.for_jev(), "active_goal": "stockpile_fuel"}
    context, questions, offered = question_batch(state, plans)
    context, questions = deepcopy(context), deepcopy(questions)
    if tamper == "description":
        context["candidate_plans"][offered[0].id]["description"] = "different plan"
    elif tamper == "shared":
        context["shared_plan_materials"]["large_proof"]["description"] = "different proof"
    elif tamper == "question":
        questions["candidate"]["description"] = "different criterion"
    else:
        context["active_goal"] = "different goal"

    class NoCall:
        def evaluate(self, *_args):
            pytest.fail("tampered request reached provider")

    with pytest.raises(ValueError, match="differs"):
        select_plan(NoCall(), state, plans, prepared_batch=(context, questions, offered))


@pytest.mark.parametrize("left,right", [(True, 1), (1, 1.0)])
def test_compaction_keeps_json_distinct_material_values(left, right):
    from jev_factorio.judgments import _compact_plan_documents
    plans = [Plan(id=f"p{index}", goal="stockpile_fuel", description="candidate",
                  steps=(Step("mine_coal", "inventory", "coal", 5),),
                  materials={"proof": {"value": value, "text": "x" * 1000}})
             for index, value in enumerate((left, right))]
    documents, shared = _compact_plan_documents(plans)
    assert shared == {}
    assert type(documents["p0"]["materials"]["proof"]["value"]) is type(left)
    assert type(documents["p1"]["materials"]["proof"]["value"]) is type(right)


def test_prepared_offered_objects_cannot_replace_json_distinct_original_plan():
    from dataclasses import replace
    original = Plan("p", "stockpile_fuel", "candidate",
                    (Step("mine_coal", "inventory", "coal", 5),),
                    materials={"proof": {"flag": True}})
    tampered = replace(original, materials={"proof": {"flag": 1}})
    assert original == tampered  # Python equality alone is not the contract.
    state = {"facts": MockBackend().observe().for_jev(), "active_goal": "stockpile_fuel"}
    batch = question_batch(state, [tampered])
    with pytest.raises(ValueError, match="Invalid"):
        select_plan(MockJevClient(), state, [original], prepared_batch=batch)
