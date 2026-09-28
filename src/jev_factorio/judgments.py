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
        evidence = context.get('candidate_evidence') or {}
        facts = state.get('facts')
        tick = facts.get('tick') if isinstance(facts, dict) else None
        local = state.get('local_objective')
        primary = local.get('primary_target') if isinstance(local, dict) else None
        target = primary.get('item') if isinstance(primary, dict) else None
        def observed_gather(row):
            start = row.get('gather_start_evidence') or {}
            return (start.get('resource_in_current_observation') is True
                    and start.get('fair_target_identity_observed') is True)
        current_prerequisite = any(
            isinstance(target, str) and target
            and row.get('work_scope') == 'immediate' and observed_gather(row)
            and isinstance(row.get('raw_prerequisite'), dict)
            and row['raw_prerequisite'].get('observed_tick') == tick
            and isinstance(row['raw_prerequisite'].get('planner_item_path'), list)
            and row['raw_prerequisite']['planner_item_path'][:1] == [target]
            for row in evidence.values())
        unlinked_lookahead = any(
            row.get('work_scope') == 'lookahead' and observed_gather(row)
            and row.get('raw_prerequisite') is None
            and row.get('fuel_prerequisite') is None
            and row.get('urgency') == 0
            for row in evidence.values())
        choice_priority_hint = (
            " When current observed start facts support both an immediate raw "
            "prerequisite with a current planner recipe path and an unlinked "
            "lookahead bulk gather with no observed urgency, favor the immediate "
            "prerequisite unless another current fact justifies the lookahead work. "
            "A larger pickup quantity alone is not such a fact. Later crafting "
            "and output still require fresh native verification."
            if current_prerequisite and unlinked_lookahead else "")
        craft_choice_hint = ""
        if len(selected) == 1 and type(tick) is int and isinstance(target, str) and target:
            plan = selected[0]
            row = evidence.get(plan.id)
            if isinstance(row, dict) and len(plan.steps) == 1:
                step = plan.steps[0]
                start = row.get('craft_start_evidence')
                dependency = row.get('craft_dependency')
                path = dependency.get('planner_item_path') if isinstance(dependency, dict) else None
                expected = (start.get('expected_products_after_native_verification')
                            if isinstance(start, dict) else None)
                if (step.action == 'factory_craft_job'
                        and isinstance(step.item, str) and step.item
                        and isinstance(step.parameters, dict)
                        and isinstance(step.parameters.get('receipt'), str)
                        and bool(step.parameters['receipt'])
                        and row.get('work_scope') == 'immediate'
                        and row.get('unknowns') == []
                        and isinstance(start, dict)
                        and start.get('observed_tick') == tick
                        and start.get('native_recipe') == step.parameters.get('recipe')
                        and all(start.get(key) is True for key in (
                            'input_costs_match_native_recipe', 'inputs_in_inventory_now',
                            'recipe_unlocked_and_handcraftable', 'player_connected_and_bound',
                            'crafting_queue_empty', 'craft_job_protocol_ready',
                            'native_receipt_required_for_completion'))
                        and isinstance(expected, dict)
                        and type(expected.get(step.item)) is int
                        and expected[step.item] > 0
                        and isinstance(dependency, dict)
                        and dependency.get('observed_tick') == tick
                        and dependency.get('current_craft_product') == step.item
                        and dependency.get('basis') == (
                            'current_recursive_planner_provenance_and_native_recipe')
                        and isinstance(path, list) and len(path) >= 2
                        and path[0] == target and path[-1] == step.item):
                    craft_choice_hint = (
                        " The sole offered handcraft has current native recipe, "
                        "carried-input, actor, queue, and receipt-protocol start facts "
                        "plus a current planner path to the local target. Prefer this "
                        "bounded craft over observe unless another current fact "
                        "identifies a specific missing or contradictory start condition. "
                        "Do not require certainty that the eventual target will finish; "
                        "this craft and later output still require native receipt and "
                        "fresh postcondition checks.")
        questions = {
            "candidate": {
                "type": "choice",
                "instructions": (f"Choose the best supplied candidate plan for `{objective}` using "
                                 "`facts`, `candidate_evidence` when present, and `history`. "
                                 "Select observe only when evidence needed to start is missing. "
                                 "For a gather candidate, `gather_start_evidence` when present "
                                 "summarizes the current observed resource and fair target; an estimated "
                                 "travel distance is not proof of arrival. Uncertain later "
                                 "crafting, research, or travel outcome is checked after this "
                                 "bounded step and does not by itself require another observation. "
                                 "Compare observe by the specific start fact it could resolve now: "
                                 "when the current gather has an observed resource and fair target "
                                 "and no start fact is missing, repeating the same observation alone "
                                 "does not establish a future travel or crafting outcome. Keep observe "
                                 "available for a genuinely missing or disputed start fact. "
                                 "For a handcraft, `craft_start_evidence` describes current inputs "
                                 "and actor readiness; expected output still needs native verification. "
                                 "Report confidence in choosing the best next action from this "
                                 "observed frontier, not confidence in completing the ultimate goal. "
                                 "Do not assume other questions' answers are available."
                                 + choice_priority_hint + craft_choice_hint),
                "criteria": {**{p.id: p.description for p in selected},
                             "observe": "Gather another observation without mutating the factory"},
            }
        }
        for plan in selected:
            pointer = f"`candidate_plans[{json.dumps(plan.id)}]`"
            row = evidence.get(plan.id)
            row = row if isinstance(row, dict) else {}
            placement_start = row.get('placement_start_evidence')
            placement_dependency = row.get('placement_dependency')
            placement_step = plan.steps[0] if len(plan.steps) == 1 else None
            placement_path = (placement_dependency.get('planner_item_path')
                              if isinstance(placement_dependency, dict) else None)
            qualified_placement = (
                isinstance(placement_start, dict)
                and isinstance(placement_dependency, dict)
                and placement_step is not None
                and placement_step.action == 'factory_place'
                and isinstance(placement_step.parameters, dict)
                and placement_step.parameters.get('name') == 'stone-furnace'
                and isinstance(placement_step.parameters.get('role'), str)
                and placement_step.parameters['role'].startswith('recipe:')
                and isinstance(placement_step.parameters.get('anchor'), str)
                and bool(placement_step.parameters['anchor'])
                and placement_step.costs == {'stone-furnace': 1}
                and row.get('work_scope') == 'immediate'
                and row.get('unknowns') == []
                and type(tick) is int
                and placement_start.get('observed_tick') == tick
                and placement_dependency.get('observed_tick') == tick
                and placement_start.get('site_state') == 'proposed'
                and all(placement_start.get(key) is True for key in (
                    'native_offer_checked_current_site_clearance',
                    'paid_furnace_in_inventory_now', 'no_source_owned_at_role_now',
                    'player_connected_and_bound_now', 'crafting_queue_empty_now',
                    'native_preflight_rechecks_offer_and_actor'))
                and placement_start.get('site_anchor') ==
                    placement_step.parameters.get('anchor')
                and placement_start.get('source_role') ==
                    placement_step.parameters.get('role')
                and placement_dependency.get('machine_for_recipe') ==
                    placement_step.parameters.get('role')
                and placement_dependency.get('basis') ==
                    'current_recursive_planner_and_validated_native_site'
                and isinstance(target, str) and bool(target)
                and isinstance(placement_path, list) and len(placement_path) >= 2
                and placement_path[0] == target
                and placement_path[-1] ==
                    placement_step.parameters['role'].removeprefix('recipe:'))
            craft_hint = (
                " `craft_start_evidence` shows the current actor, queue, recipe, "
                "and carried ingredients needed to start this handcraft; "
                "`craft_dependency` traces its product along the current planner "
                "recipe path to the local target. Score this bounded intermediate "
                "product for its evidenced contribution, without requiring it to "
                "finish the target. The craft and later production still need "
                "fresh native receipt and precondition checks."
                if (((state.get('candidate_evidence') or {}).get(plan.id) or {}).get('craft_start_evidence')
                    and ((state.get('candidate_evidence') or {}).get(plan.id) or {}).get('craft_dependency'))
                else ""
            )
            place_hint = (
                " `placement_start_evidence` and `placement_dependency` bind this "
                "paid furnace to a currently offered, unoccupied source role on "
                "the local planner path. Placing it provides evidenced bounded "
                "capacity (score level 1); it does not yet demonstrate downstream "
                "production-blocker removal (level 2). Another current contrary "
                "fact can lower the score. Native placement receipt, fuel, input, "
                "transport and output remain unverified and need fresh checks."
                if qualified_placement
                else ""
            )
            fuel_hint = (
                " `fuel_prerequisite` ties this bounded coal pickup to the current "
                "owned burner's startup need; later transfer and production remain unverified."
                if ((state.get('candidate_evidence') or {}).get(plan.id) or {}).get('fuel_prerequisite')
                else ""
            )
            transfer_start = ((state.get('candidate_evidence') or {}).get(plan.id) or {}).get(
                'fuel_transfer_start_evidence')
            transfer_hint = (
                " `fuel_transfer_start_evidence` ties the paid coal transfer to the current "
                "owned burner, carried quantity, planner path, and native receipt. The transfer "
                "and later production still require native verification."
                if transfer_start else ""
            )
            recipe_input_start = ((state.get('candidate_evidence') or {}).get(plan.id) or {}).get(
                'recipe_input_transfer_start_evidence')
            input_hint = (
                " `recipe_input_transfer_start_evidence` ties this paid ingredient transfer "
                "to the current planner path, native recipe, owned machine, carried input, "
                "and planned receipt ID. It does not prove transfer or output; native "
                "verification remains required."
                if recipe_input_start else ""
            )
            pickup_start = row.get('output_pickup_start_evidence')
            pickup_step = plan.steps[0] if len(plan.steps) == 1 else None
            pickup_path = (pickup_start.get('planner_item_path')
                           if isinstance(pickup_start, dict) else None)
            qualified_pickup = (
                pickup_step is not None and pickup_step.action == 'factory_extract'
                and pickup_step.effect == 'transfer' and pickup_step.costs == {}
                and isinstance(pickup_step.parameters, dict)
                and row.get('work_scope') == 'immediate' and row.get('unknowns') == []
                and isinstance(pickup_start, dict) and pickup_start.get('observed_tick') == tick
                and pickup_start.get('basis') ==
                    'current_planner_output_and_owned_native_machine'
                and pickup_start.get('player_connected_and_bound_now') is True
                and pickup_start.get('native_pickup_and_inventory_delta_require_verification') is True
                and pickup_start.get('owned_source_role') == pickup_step.parameters.get('role')
                and pickup_start.get('ready_output_item') == pickup_step.parameters.get('item')
                and pickup_start.get('planned_pickup_quantity') ==
                    pickup_step.parameters.get('quantity')
                and pickup_start.get('planned_native_receipt_id') ==
                    pickup_step.parameters.get('receipt')
                and type(pickup_start.get('ready_output_quantity_now')) is int
                and type(pickup_start.get('planned_pickup_quantity')) is int
                and pickup_start['ready_output_quantity_now'] >=
                    pickup_start['planned_pickup_quantity'] > 0
                and isinstance(target, str) and bool(target)
                and isinstance(pickup_path, list) and len(pickup_path) >= 1
                and pickup_path[0] == target
                and pickup_path[-1] == pickup_step.parameters.get('item'))
            pickup_hint = (
                " `output_pickup_start_evidence` ties already observed output at an "
                "owned native machine to the current local planner path and planned "
                "receipt. Collecting that output supplies a bounded useful intermediate "
                "(score level 1); it does not finish the downstream target or prove "
                "pickup. Another current contrary fact can lower the score. The native "
                "pickup receipt and player inventory delta still require verification."
                if qualified_pickup else ""
            )
            questions[plan.id + "/benefit"] = {
                "type": "score",
                "instructions": (
                    f"How directly do the steps in {pointer} advance `{objective}` "
                    "given `facts`, current `candidate_evidence`, and `execution_contract`? Do not demand a full-game plan "
                    "from one bounded local production action. A current "
                    "`raw_prerequisite` is evidence that gathering supplies an input to "
                    "the named native recipe, not that the later craft already happened."
                    + craft_hint + place_hint + fuel_hint + transfer_hint + input_hint
                    + pickup_hint
                ),
                "criteria": ([
                    "No demonstrated contribution to the bounded production objective",
                    "Supplies useful inputs, a current planner-linked intermediate craft, "
                    "or evidenced capacity for the bounded task",
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
                    "Only moves, gathers resources, waits, fuels an existing machine, "
                    "or handcrafts from carried inputs without changing existing entities",
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
                    "research or victory is not required for gathering an observed raw resource "
                    "or fueling a drill. For a gather, use current `gather_start_evidence` "
                    "when present; do not treat the unverified travel outcome as a missing "
                    "start fact. "
                    "Future action outcomes will be verified after execution, not assumed now."
                    + (" For a handcraft, use current `craft_start_evidence` to judge "
                       "the actor, queue, native recipe, and carried ingredients "
                       "needed to start. The output still requires native receipt "
                       "verification; its future completion is not a missing "
                       "start observation."
                       if ((state.get('candidate_evidence') or {}).get(plan.id) or {}).get(
                           'craft_start_evidence') else "")
                    + (" For a paid fuel transfer, `fuel_transfer_start_evidence` describes "
                       "the current carried coal, owned burner, and exact receipt. Judge "
                       "start facts from those values; the future transfer outcome is "
                       "verified by the native receipt."
                       if transfer_start else "")
                    + (" For a paid recipe-input transfer, "
                       "`recipe_input_transfer_start_evidence` describes current "
                       "carried input, owned machine, recipe, and planned receipt ID. "
                       "Judge only missing start facts; the transfer and output "
                       "still need native verification."
                       if recipe_input_start else "")
                    + (" For a current output pickup, `output_pickup_start_evidence` "
                       "records ready output, owned source, actor, planner path and "
                       "planned receipt. Judge missing start facts from those values; "
                       "the future pickup and inventory delta require native verification."
                       if qualified_pickup else "")
                    + (" For a placement, `placement_start_evidence` combines a current "
                       "surveyed site offer with observed actor/queue facts. Judge missing "
                       "start facts from those "
                       "values; an unverified walking path or future build receipt is not a "
                       "missing start observation."
                       if placement_start else "")
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
