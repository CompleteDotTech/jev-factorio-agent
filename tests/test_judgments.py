from copy import deepcopy
import json

import pytest

from jev_factorio.backends.mock import MockBackend
from jev_factorio.jev_client import MockJevClient, make_client
from jev_factorio.judgments import InvalidJudgment, question_batch, select_plan, validate_answers
from jev_factorio.skills import Plan, Step, compile_plans


def batch():
    snapshot = MockBackend().observe()
    plans, _ = compile_plans("stockpile_fuel", snapshot)
    state = {"facts": snapshot.for_jev(), "active_goal": "stockpile_fuel"}
    context, questions, _ = question_batch(state, plans)
    return plans, context, questions


def test_explicit_question_references_and_full_mock_distributions():
    plans, context, questions = batch()
    assert len(questions) == 1 + 4 * len(plans)
    for plan in plans:
        assert plan.id in questions[plan.id + "/benefit"]["instructions"]
    answers = MockJevClient().evaluate(context, questions)
    validate_answers(questions, answers)
    assert all(sum(a["probabilities"].values()) == 1
               for a in answers.values() if "probabilities" in a)


def test_batched_selection_is_labelled_as_mock():
    plans, context, _ = batch()
    result = select_plan(MockJevClient(), context, plans)
    assert result.plan_id in {p.id for p in plans}
    assert result.source == "mock"
    assert result.utilities


@pytest.mark.parametrize("change", ["missing", "extra", "nan", "negative", "sum", "label", "type", "confidence"])
def test_invalid_answers_fail_closed(change):
    _, context, questions = batch()
    answers = deepcopy(MockJevClient().evaluate(context, questions))
    chosen = answers["candidate"]["choice"]
    if change == "missing":
        del answers["candidate"]
    elif change == "extra":
        answers["unsolicited"] = {"type": "noul", "noul": 1}
    elif change == "nan":
        answers["candidate"]["probabilities"][chosen] = float("nan")
    elif change == "negative":
        answers["candidate"]["probabilities"][chosen] = -1
    elif change == "sum":
        answers["candidate"]["probabilities"][chosen] = 0.8
    elif change == "label":
        answers["candidate"]["choice"] = "launch_rocket_now"
    elif change == "type":
        answers["candidate"]["type"] = "score"
    else:
        answers["candidate"]["confidence"] = 1.1
    with pytest.raises(InvalidJudgment):
        validate_answers(questions, answers)


def test_low_confidence_or_missing_evidence_abstains():
    plans, context, _ = batch()

    class LowConfidence(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            answers["candidate"]["confidence"] = 0.1
            return answers

    class MissingEvidence(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for key in answers:
                if key.endswith("/needs_observation"):
                    answers[key]["noul"] = 0.9
            return answers

    assert select_plan(LowConfidence(), context, plans).plan_id is None
    assert select_plan(MissingEvidence(), context, plans).plan_id is None


def test_low_choice_reports_the_other_rejections_in_the_native_failure():
    """Sanitized 0161 response: the near-tie did not contain eligible plans."""
    plans = [Plan(name, "stockpile_fuel", "gather",
                  (Step("mine_coal", "inventory", "coal", 5),))
             for name in ("direct", "investment")]

    class CapturedConfidence(MockJevClient):
        calls = 0

        def evaluate(self, state, questions):
            self.calls += 1
            answers = super().evaluate(state, questions)
            answers["candidate"] = {
                "type": "choice", "choice": "direct", "confidence": 0.34,
                "probabilities": {"direct": 0.56, "investment": 0.41, "observe": 0.03},
            }
            answers["direct/useful_progress"] = {
                "type": "choice", "choice": "useful", "confidence": 0.26,
                "probabilities": {"useful": 0.63, "unsupported": 0.37},
            }
            answers["investment/useful_progress"] = {
                "type": "choice", "choice": "unsupported", "confidence": 0.42,
                "probabilities": {"useful": 0.29, "unsupported": 0.71},
            }
            _benefit_answers(answers, "direct", {"0": 0.04, "1": 0.72, "2": 0.24}, 0.57)
            _benefit_answers(answers, "investment", {"0": 0.42, "1": 0.56, "2": 0.02}, 0.35)
            return answers

    client = CapturedConfidence()
    decision = select_plan(client, {}, plans)
    assert client.calls == 1
    assert decision.plan_id is None and decision.utilities == {}
    assert decision.source == "observe" and decision.reason == "low choice confidence"
    assert decision.diagnostics["outcome"] == "low_choice_confidence"
    assert decision.diagnostics["candidate_rejections"] == {
        "direct": ["low_usefulness_confidence"],
        "investment": ["no_demonstrated_progress"],
    }
    assert decision.diagnostics["usefulness_gate"]["direct"] == {
        "choice": "useful", "confidence": 0.26, "floor": 0.45, "passed": False,
    }
    assert decision.diagnostics["benefit_gate"]["direct"]["eligibility_authority"] is False


@pytest.mark.parametrize("abstain", [False, True])
def test_global_choice_rejection_still_blocks_independently_qualified_plans(abstain):
    plans, context, _ = batch()

    class GlobalRejection(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            choice = answers["candidate"]
            if abstain:
                choice["choice"] = "observe"
                choice["probabilities"] = {name: float(name == "observe")
                                           for name in choice["probabilities"]}
            else:
                choice["confidence"] = 0.34
            return answers

    decision = select_plan(GlobalRejection(), context, plans)
    assert decision.plan_id is None and decision.utilities == {}
    assert decision.diagnostics["outcome"] == (
        "model_abstention" if abstain else "low_choice_confidence")
    assert decision.diagnostics["candidate_rejections"] == {}
    assert all(gate["passed"] for gate in decision.diagnostics["usefulness_gate"].values())


def test_request_count_and_byte_budget_are_bounded():
    plans = [Plan(str(i), "fuel", "gather", (Step("mine_coal", "inventory", "coal", 5),))
             for i in range(300)]
    context, questions, offered = question_batch({}, plans)
    assert 1 <= len(offered) <= 16
    assert len(questions["candidate"]["criteria"]) == len(offered) + 1
    assert len(json.dumps({"state": context, "questions": questions},
                          ensure_ascii=False, allow_nan=False).encode("utf-8")) <= 32000
    with pytest.raises(ValueError, match="byte budget"):
        question_batch({"facts": "x" * 50000}, plans)
    with pytest.raises(ValueError):
        question_batch({}, plans, max_candidates=255)


def test_strict_client_creation_never_silently_uses_mock(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    with pytest.raises(ValueError, match="credentials"):
        make_client(allow_mock=False)
    assert isinstance(make_client(), MockJevClient)


def test_provider_model_can_be_pinned_without_network(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    assert make_client(allow_mock=False, model="jev-1.13.0").model == "jev-1.13.0"


@pytest.mark.parametrize("change", ["bad-legend", "bad-weighted-score", "unhashable-choice"])
def test_contradictory_or_ill_typed_responses_rejected(change):
    _, state, questions = batch()
    answers = MockJevClient().evaluate(state, questions)
    score_key = next(key for key in answers if key.endswith("/benefit"))
    if change == "bad-legend":
        answers[score_key]["legend"] = {}
    elif change == "bad-weighted-score":
        answers[score_key]["score"] = 0
    else:
        answers["candidate"]["choice"] = ["bad"]
    with pytest.raises(InvalidJudgment):
        validate_answers(questions, answers)


def test_maximum_choice_uses_254_candidates_plus_observe():
    plans = [Plan(str(i), "fuel", "gather", (Step("mine_coal", "inventory", "coal", 5),))
             for i in range(300)]
    _, questions, offered = question_batch({}, plans, max_candidates=254, max_bytes=1000000)
    assert len(offered) == 254
    assert len(questions["candidate"]["criteria"]) == 255
    assert len(questions) == 1017  # schema construction only; no live provider call


@pytest.mark.parametrize(("probabilities", "score"), [
    ([0.56, 0.28, 0.07, 0.05, 0.03], 0.71),
    ([0.63, 0.27, 0.05, 0.03, 0.02], 0.56),
])
def test_observed_provider_rounding_is_consistent(probabilities, score):
    levels = ["none", "minor", "moderate", "major", "unacceptable"]
    questions = {"impact": {"type": "score", "criteria": levels}}
    answers = {"impact": {
        "type": "score", "score": score, "confidence": 0.53,
        "legend": {str(index): level for index, level in enumerate(levels)},
        "probabilities": {str(index): value for index, value in enumerate(probabilities)},
    }}
    original = deepcopy(answers)
    validate_answers(questions, answers, quantum=0.01)
    assert answers == original
    answers["impact"]["score"] = 1.2
    with pytest.raises(InvalidJudgment, match="conflicts"):
        validate_answers(questions, answers, quantum=0.01)


def test_provider_rounding_does_not_allow_invalid_distribution():
    _, state, questions = batch()
    answers = MockJevClient().evaluate(state, questions)
    answers["candidate"]["probabilities"][answers["candidate"]["choice"]] = 0.8
    with pytest.raises(InvalidJudgment, match="sum"):
        validate_answers(questions, answers, quantum=0.01)


def test_provider_rounding_does_not_disable_confidence_gate():
    plans, state, _ = batch()

    class RoundedLowConfidence(MockJevClient):
        answer_quantum = 0.01

        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            answers["candidate"]["confidence"] = 0.1
            return answers

    assert select_plan(RoundedLowConfidence(), state, plans).plan_id is None


def _benefit_answers(client_answers: dict, plan_id: str, probabilities: dict,
                     confidence: float) -> dict:
    expected = sum(int(level) * value for level, value in probabilities.items())
    client_answers[plan_id + "/benefit"].update(
        probabilities=probabilities, score=expected, confidence=confidence)
    return client_answers


def test_benefit_gate_measures_support_for_any_contribution():
    from jev_factorio.judgments import benefit_gate
    split = {"type": "score", "confidence": 0.32,
             "probabilities": {"0": 0.01, "1": 0.42, "2": 0.57}}
    gate = benefit_gate(split, 0.45)
    assert gate["passed"] is True
    assert gate["support"] == pytest.approx(0.99)
    assert gate["reported_confidence"] == pytest.approx(0.32)
    assert gate["floor"] == pytest.approx(0.45)
    doubtful = {"type": "score", "confidence": 0.9,
                "probabilities": {"0": 0.5, "1": 0.3, "2": 0.2}}
    assert benefit_gate(doubtful, 0.45)["passed"] is False
    tie = {"type": "score", "confidence": 0.9,
           "probabilities": {"0": 0.4, "1": 0.4, "2": 0.2}}
    assert benefit_gate(tie, 0.45)["passed"] is False
    weak = {"type": "score", "confidence": 0.9,
            "probabilities": {"0": 0.3, "1": 0.35, "2": 0.35}}
    assert benefit_gate(weak, 0.45)["passed"] is True
    assert benefit_gate(weak, 0.75)["passed"] is False


def test_positive_level_split_with_low_reported_confidence_is_selected():
    plans, context, _ = batch()
    target = plans[0].id

    class LevelSplit(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for key in list(answers):
                if key.endswith("/benefit"):
                    _benefit_answers(answers, key[:-len("/benefit")],
                                     {"0": 0.01, "1": 0.42, "2": 0.57}, 0.32)
            return answers

    decision = select_plan(LevelSplit(), context, plans)
    assert decision.plan_id is not None
    assert decision.diagnostics["outcome"] == "selected"
    assert decision.diagnostics["candidate_rejections"] == {}
    gate = decision.diagnostics["benefit_gate"][target]
    assert gate["passed"] is True and gate["support"] == pytest.approx(0.99)
    assert gate["reported_confidence"] == pytest.approx(0.32)


def test_level_zero_dominant_benefit_is_rejected_despite_confident_report():
    plans, context, _ = batch()

    class NoContribution(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for key in list(answers):
                if key.endswith("/benefit"):
                    _benefit_answers(answers, key[:-len("/benefit")],
                                     {"0": 0.55, "1": 0.25, "2": 0.2}, 0.97)
            return answers

    decision = select_plan(NoContribution(), context, plans)
    assert decision.plan_id is None
    assert decision.reason == "Candidate evidence insufficient"
    assert decision.diagnostics["outcome"] == "all_candidates_rejected"
    for plan in plans:
        if plan.id in decision.diagnostics["candidate_rejections"]:
            assert decision.diagnostics["candidate_rejections"][plan.id] == [
                "low_benefit_confidence"]
            assert decision.diagnostics["benefit_gate"][plan.id]["passed"] is False
    assert decision.diagnostics["candidate_rejections"]


def test_explicit_usefulness_gate_respects_a_higher_floor():
    plans, context, _ = batch()

    class WeakSupport(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for key in list(answers):
                if key.endswith("/benefit"):
                    _benefit_answers(answers, key[:-len("/benefit")],
                                     {"0": 0.3, "1": 0.35, "2": 0.35}, 0.5)
                elif key.endswith("/useful_progress"):
                    answers[key]["confidence"] = 0.7
            return answers

    assert select_plan(WeakSupport(), context, plans).plan_id is not None
    assert select_plan(WeakSupport(), context, plans, confidence_floor=0.75).plan_id is None


def test_all_rejected_with_pruned_candidates_reports_alternatives_not_shown():
    plans = [Plan(str(i), "fuel", "gather", (Step("mine_coal", "inventory", "coal", 5),))
             for i in range(6)]

    class RejectEverything(MockJevClient):
        def evaluate(self, state, questions):
            answers = super().evaluate(state, questions)
            for key in list(answers):
                if key.endswith("/needs_observation"):
                    answers[key]["noul"] = 0.9
            return answers

    def size(count):
        context, questions, _ = question_batch({}, plans[:count], max_bytes=10 ** 6)
        return len(json.dumps({"state": context, "questions": questions},
                              ensure_ascii=False).encode("utf-8"))

    budget = (size(2) + size(3)) // 2
    _, _, offered = question_batch({}, plans, max_bytes=budget)
    assert len(offered) == 2 < len(plans)
    decision = select_plan(RejectEverything(), {}, plans, max_bytes=budget)
    assert decision.plan_id is None and decision.reason == "Candidate evidence insufficient"
    diagnostics = decision.diagnostics
    assert diagnostics["outcome"] == "all_candidates_rejected"
    assert diagnostics["alternatives_not_shown"] == diagnostics["pruned_candidate_ids"]
    assert len(diagnostics["alternatives_not_shown"]) == len(plans) - len(offered)
    assert diagnostics["max_request_bytes"] == budget
    assert 0 < diagnostics["request_bytes"] <= budget
    fits = select_plan(RejectEverything(), {}, plans, max_bytes=100000)
    assert "alternatives_not_shown" not in fits.diagnostics
    assert fits.diagnostics["pruned_candidate_ids"] == []
