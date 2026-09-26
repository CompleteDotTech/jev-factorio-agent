"""Bounded, explicit JEV candidate judgments; no implicit question dependencies."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

import requests

from .skills import Plan
from .provider_health import ProviderBlocked


class InvalidJudgment(ValueError):
    """Malformed or out-of-domain answers must not authorize an action."""


def _number(value, maximum: float = 1.0) -> float:
    if (isinstance(value, bool) or not isinstance(value, (float, int))
            or not math.isfinite(value) or not 0 <= value <= maximum):
        raise InvalidJudgment("Expected a finite in-range number")
    return float(value)


def _rounded_score_bounds(probabilities: dict, quantum: float) -> tuple[float, float]:
    lower = [max(0, probabilities[str(index)] - quantum / 2)
             for index in range(len(probabilities))]
    upper = [min(1, probabilities[str(index)] + quantum / 2)
             for index in range(len(probabilities))]

    def extreme(order):
        remaining = max(0, 1 - sum(lower))
        score = sum(index * value for index, value in enumerate(lower))
        for index in order:
            extra = min(remaining, upper[index] - lower[index])
            score += index * extra
            remaining -= extra
        return score

    return extreme(range(len(lower))), extreme(reversed(range(len(lower))))


def validate_answers(questions: dict, answers: dict, quantum: float = 0) -> None:
    if quantum not in (0, 0.01):
        raise InvalidJudgment("Unsupported answer rounding precision")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise InvalidJudgment("Missing or unexpected answers")
    for key, question in questions.items():
        answer = answers[key]
        kind = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != kind:
            raise InvalidJudgment("Answer type mismatch")
        if kind == "noul":
            _number(answer.get("noul"))
            continue
        _number(answer.get("confidence"))
        probabilities = answer.get("probabilities")
        labels = (set(question["criteria"]) if kind == "choice"
                  else {str(i) for i in range(len(question["criteria"]))})
        if not isinstance(probabilities, dict) or set(probabilities) != labels:
            raise InvalidJudgment("Incomplete answer distribution")
        values = [_number(p) for p in probabilities.values()]
        total = sum(values)
        valid_total = (
            sum(max(0, value - quantum / 2) for value in values) <= 1 + 1e-9
            and sum(min(1, value + quantum / 2) for value in values) >= 1 - 1e-9
        ) if quantum else math.isclose(total, 1, abs_tol=1e-3)
        if not valid_total:
            raise InvalidJudgment("Probabilities must sum to one")
        if kind == "choice":
            choice = answer.get("choice")
            if (not isinstance(choice, str) or choice not in labels
                    or probabilities[choice] + 1e-6 < max(probabilities.values())):
                raise InvalidJudgment("Choice must be an offered maximum-probability label")
        else:
            score = _number(answer.get("score"), len(labels) - 1)
            if answer.get("legend") != {str(i): level for i, level in enumerate(question["criteria"])}:
                raise InvalidJudgment("Score legend does not match the supplied rubric")
            expected = sum(int(key) * value for key, value in probabilities.items())
            if quantum:
                minimum, maximum = _rounded_score_bounds(probabilities, quantum)
                consistent = (score + quantum / 2 >= minimum - 1e-9
                              and score - quantum / 2 <= maximum + 1e-9)
            else:
                consistent = math.isclose(score, expected, abs_tol=0.02 + 1e-9)
            if not consistent:
                raise InvalidJudgment("Score conflicts with its probability distribution")


@dataclass
class Decision:
    plan_id: str | None
    source: str
    reason: str = ""
    state: dict = field(default_factory=dict)
    questions: dict = field(default_factory=dict)
    answers: dict = field(default_factory=dict)
    utilities: dict[str, float] = field(default_factory=dict)
    model_called: bool = False
    diagnostics: dict = field(default_factory=dict)


def question_batch(state: dict, plans: list[Plan], max_bytes: int = 32000,
                   max_candidates: int = 16) -> tuple[dict, dict, list[Plan]]:
    """Bound serialized request bytes, NOT estimated tokens or provider limits."""
    if max_bytes < 1 or not 1 <= max_candidates <= 254:
        raise ValueError("Invalid request budget")
    selected = plans[:max_candidates]
    objective = "local_objective" if "local_objective" in state else "active_goal"
    if len({p.id for p in selected}) != len(selected):
        raise ValueError("Duplicate candidate IDs")
    while selected:
        context = {
            **state, "candidate_plans": {p.id: p.to_dict() for p in selected},
            "execution_contract": (
                "These are bounded tool plans, not keyboard commands or full-game strategies. "
                "Code filters plans for current resource, inventory, and placement preconditions "
                "and checks them again before dispatch. walk_to_coal and walk_to_iron move "
                "to an observed patch; mine_coal harvests five coal into inventory; "
                "place_burner_drill consumes one drill and one chest on iron; fuel_drill "
                "inserts five carried coal. factory_* steps execute the explicit parameters "
                "using native recipes, paid inventory transfers, machines, physical connections, "
                "and research. Native crafting waits for its real queue; native machines and "
                "labs must actually produce or research. Judge the supplied local_objective when present; "
                "otherwise judge active_goal. Estimates are not facts or execution permission. "
                "Each action needs a fresh observed postcondition before it counts as success."
            ),
        }
        if "candidate_evidence" in context:
            context["candidate_evidence"] = {p.id: state["candidate_evidence"][p.id]
                                             for p in selected if p.id in state["candidate_evidence"]}
        if "deterministic_ranking" in context:
            context["deterministic_ranking"] = [key for key in state["deterministic_ranking"]
                                                if key in context["candidate_plans"]]
        questions = {
            "candidate": {
                "type": "choice",
                "instructions": (f"Choose the best supplied candidate plan for `{objective}` using "
                                 "`facts`, `candidate_evidence` when present, and `history`. "
                                 "Select observe only when evidence needed to start is missing. "
                                 "Do not assume other questions' answers are available."),
                "criteria": {**{p.id: p.description for p in selected},
                             "observe": "Gather another observation without mutating the factory"},
            }
        }
        for plan in selected:
            pointer = f"`candidate_plans[{json.dumps(plan.id)}]`"
            questions[plan.id + "/benefit"] = {
                "type": "score",
                "instructions": (
                    f"How directly do the steps in {pointer} advance `{objective}` "
                    "given `facts` and `execution_contract`? Do not demand a full-game plan "
                    "from one bounded local production action."
                ),
                "criteria": ([
                    "No demonstrated contribution to the bounded production objective",
                    "Supplies useful inputs or evidenced capacity for the bounded task",
                    "Directly removes an observed production blocker or prevents due starvation",
                ] if objective == "local_objective" else [
                    "The steps do not improve the active goal's required state",
                    "The steps make partial progress but leave a required action unplanned",
                    "The steps supply all actions needed to satisfy the active goal",
                ]),
            }
            questions[plan.id + "/disruption"] = {
                "type": "score",
                "instructions": (
                    f"How disruptive are the steps in {pointer} to the existing factory "
                    "in `facts`, under `execution_contract`?"
                ),
                "criteria": [
                    "Only moves, gathers resources, waits, or fuels an existing machine",
                    "Places new machinery without removing any existing entity",
                    "Stops, removes, or rebuilds existing factory infrastructure",
                ],
            }
            questions[plan.id + "/needs_observation"] = {
                "type": "noul",
                "instructions": (
                    f"Is a fact required to start the next step of {pointer} missing "
                    "from `facts`, given `execution_contract`? Consider only resource location, "
                    "carried materials, and the entities used by that step. Unknown later-game "
                    "research or victory is not required for mining coal or fueling a drill. "
                    "Future action outcomes will be verified after execution, not assumed now."
                ),
            }
        size = len(json.dumps({"state": context, "questions": questions},
                              ensure_ascii=False, allow_nan=False).encode("utf-8"))
        if size <= max_bytes:
            return context, questions, selected
        selected = selected[:-1]
    raise ValueError("Decision request exceeds byte budget or has no candidates")


def select_plan(client, state: dict, plans: list[Plan], confidence_floor: float = 0.45,
                max_bytes: int = 32000) -> Decision:
    _number(confidence_floor)
    context, questions, offered = question_batch(state, plans, max_bytes=max_bytes)
    diagnostics = {"schema": 1, "input_candidates": len(plans),
                   "offered_candidates": len(offered),
                   "pruned_candidate_ids": [p.id for p in plans if p not in offered],
                   "candidate_rejections": {}}
    try:
        answers = client.evaluate(context, questions)
    except ProviderBlocked as error:
        return Decision(None, "observe", str(error), context, questions,
                        model_called=error.called,
                        diagnostics={**diagnostics, "outcome": "provider_blocked",
                                     "provider": error.state})
    except (requests.Timeout, requests.ConnectionError) as error:
        return Decision(None, "observe", f"Transient provider failure: {type(error).__name__}",
                        context, questions, model_called=True,
                        diagnostics={**diagnostics, "outcome": "provider_failure"})
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        if status is None or not (500 <= status <= 599 or status in {408, 429}):
            raise
        return Decision(None, "observe", f"Transient provider failure: HTTP {status}",
                        context, questions, model_called=True,
                        diagnostics={**diagnostics, "outcome": "provider_failure"})
    except ValueError as error:
        return Decision(None, "observe", f"Invalid provider payload: {type(error).__name__}",
                        context, questions, model_called=True,
                        diagnostics={**diagnostics, "outcome": "invalid_provider_payload"})
    try:
        validate_answers(questions, answers, quantum=getattr(client, "answer_quantum", 0))
    except InvalidJudgment as error:
        return Decision(None, "observe", str(error), context, questions,
                        answers if isinstance(answers, dict) else {}, model_called=True,
                        diagnostics={**diagnostics, "outcome": "invalid_answer"})
    choice = answers["candidate"]
    if choice["choice"] == "observe" or choice["confidence"] < confidence_floor:
        outcome = "model_abstention" if choice["choice"] == "observe" else "low_choice_confidence"
        return Decision(None, "observe", outcome.replace("_", " "),
                        context, questions, answers, model_called=True,
                        diagnostics={**diagnostics, "outcome": outcome})
    utilities = {}
    for plan in offered:
        benefit = answers[plan.id + "/benefit"]
        disruption = answers[plan.id + "/disruption"]
        rejected = []
        if answers[plan.id + "/needs_observation"]["noul"] >= 0.5:
            rejected.append("missing_start_evidence")
        if benefit["confidence"] < confidence_floor:
            rejected.append("low_benefit_confidence")
        if disruption["confidence"] < confidence_floor:
            rejected.append("low_disruption_confidence")
        if rejected:
            diagnostics["candidate_rejections"][plan.id] = rejected
            continue
        # Ranking heuristic, NOT a probability of plan success or game victory.
        benefit_maximum = len(questions[plan.id + "/benefit"]["criteria"]) - 1
        disruption_maximum = len(questions[plan.id + "/disruption"]["criteria"]) - 1
        utilities[plan.id] = (choice["probabilities"][plan.id]
                              + benefit["score"] / (2 * benefit_maximum)
                              - disruption["score"] / (4 * disruption_maximum)
                              - len(plan.steps) * 0.02)
    selected = max(utilities, key=utilities.get) if utilities else None
    source = "mock" if getattr(client, "is_mock", False) else "jev"
    return Decision(selected, source if selected else "observe",
                    "" if selected else "Candidate evidence insufficient",
                    context, questions, answers, utilities, model_called=True,
                    diagnostics={**diagnostics, "outcome": "selected" if selected else "all_candidates_rejected"})
