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


def _qualified_utility_lab_dependency(plan, row, local, tick) -> bool:
    if not isinstance(row, dict) or not isinstance(local, dict):
        return False
    step = plan.steps[0] if len(plan.steps) == 1 else None
    dependency = row.get('utility_lab_research_dependency')
    primary = local.get('primary_target')
    technology_target = primary.get('primary_target') if isinstance(primary, dict) else None
    technology = (technology_target.get('technology')
                  if isinstance(technology_target, dict) else None)
    return (
        step is not None
        and step.action == 'factory_place'
        and step.parameters == {'role': 'utility:lab', 'name': 'lab', 'anchor': 'factory'}
        and step.costs == {'lab': 1}
        and row.get('work_scope') == 'immediate'
        and isinstance(primary, dict)
        and primary.get('kind') == 'research_prerequisite'
        and primary.get('ultimate_goal') == plan.goal
        and primary.get('immediate_prerequisite') == 'utility:lab'
        and primary.get('observed_tick') == tick
        and primary.get('basis') == 'current_capability_research_plan'
        and type(tick) is int
        and isinstance(dependency, dict)
        and dependency.get('observed_tick') == tick
        and dependency.get('technology') == technology
        and dependency.get('basis') ==
            'same_tick_capability_research_plan_and_paid_lab_prerequisite'
        and all(dependency.get(key) is True for key in (
            'technology_not_researched_now', 'technology_unlocks_basic_assembler',
            'current_research_idle', 'current_technology_prerequisites_satisfied',
            'lab_required_by_native_research_walk', 'utility_lab_absent_now',
            'player_connected_and_bound_now', 'crafting_queue_empty_now',
            'placement_site_clearance_unknown_until_dispatch',
            'travel_and_arrival_unverified',
            'existing_native_action_performs_bounded_search_and_fresh_build_checks',
            'native_build_result_and_fresh_role_postcondition_required',
            'lab_power_and_research_require_later_native_verification'))
        and dependency.get('native_placement_site_preflight_performed') is False
        and isinstance(row.get('unknowns'), list)
        and {'placement_site:factory_place', 'travel:factory_place'} <=
            set(row.get('unknowns', []))
        and type(dependency.get('paid_lab_in_inventory_now')) is int
        and dependency['paid_lab_in_inventory_now'] >= 1
    )


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
            start = row.get('gather_start_evidence')
            if not isinstance(start, dict):
                return False
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
        bill_craft = any(
            isinstance(row, dict) and row.get('work_scope') == 'lookahead'
            and row.get('unknowns') == [] and row.get('urgency') == 0
            and isinstance(row.get('shared_bill_craft'), dict)
            and row['shared_bill_craft'].get('observed_tick') == tick
            and row['shared_bill_craft'].get('local_target_item') == target
            and row['shared_bill_craft'].get('forecast_is_not_paid_stock_or_completed_output') is True
            for row in evidence.values())
        bill_craft_hint = (
            " The lookahead handcraft has a current catalog-bill shortfall and "
            "receipt-tracked start facts. Compare its bounded contribution with "
            "the immediate raw prerequisite: an admitted receipt-tracked job "
            "may overlap a later independent gather, but overlap and output are not "
            "yet verified. Do not treat lack of a recursive craft path alone as "
            "evidence that this bill-linked craft is useless."
            if current_prerequisite and bill_craft else "")
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
        utility_lab_choice_hint = ""
        for candidate_plan in selected:
            candidate_row = evidence.get(candidate_plan.id)
            if not _qualified_utility_lab_dependency(
                    candidate_plan, candidate_row, local, tick):
                continue
            utility_lab_choice_hint += (
                " This paid lab is the current planner's immediate prerequisite for "
                "starting the named, enabled capability technology that unlocks the "
                "basic assembler. The native placement site and walking outcome have "
                "not been observed: the existing placement action performs its bounded "
                "native search and fresh build checks; only the native action result and "
                "fresh role observation verify placement. "
                "Judge this next action from its current paid item, absent role, idle bound "
                "actor, and exact research dependency; do not infer a clear site, arrival, "
                "lab power, completed research, or assembler. Observe only for a specific "
                "missing current start fact, not to demand certainty about those later outcomes."
            )
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
                                 + choice_priority_hint + bill_craft_hint
                                 + craft_choice_hint + utility_lab_choice_hint),
                "criteria": {**{p.id: p.description for p in selected},
                             "observe": "Gather another observation without mutating the factory"},
            }
        }
        for plan in selected:
            pointer = f"`candidate_plans[{json.dumps(plan.id)}]`"
            row = evidence.get(plan.id)
            row = row if isinstance(row, dict) else {}
            raw = row.get('raw_prerequisite')
            raw_path = raw.get('planner_item_path') if isinstance(raw, dict) else None
            gather_start = row.get('gather_start_evidence')
            gather_step = plan.steps[0] if len(plan.steps) == 1 else None
            gather_parameters = gather_step.parameters if gather_step is not None else None
            qualified_raw_gather = (
                len(selected) == 1 and gather_step is not None
                and gather_step.action == 'factory_gather'
                and gather_step.effect == 'inventory'
                and gather_step.costs in (None, {})
                and isinstance(gather_parameters, dict)
                and isinstance(gather_parameters.get('resource'), str)
                and bool(gather_parameters['resource'])
                and gather_parameters['resource'] == gather_step.item
                and row.get('work_scope') == 'immediate'
                and row.get('unknowns') == [] and row.get('reasons') == []
                and type(row.get('urgency')) is int and row['urgency'] == 0
                and row.get('research_deadline_tick') is None
                and row.get('requires_investment') is False
                and type(tick) is int and isinstance(target, str) and bool(target)
                and isinstance(raw, dict) and isinstance(gather_start, dict)
                and raw.get('observed_tick') == tick
                and raw.get('basis') ==
                    'current_planner_dependency_and_native_catalog_recipe'
                and raw.get('later_steps_require_fresh_native_preconditions') is True
                and isinstance(raw_path, list) and 2 <= len(raw_path) <= 32
                and all(isinstance(item, str) and bool(item) for item in raw_path)
                and raw_path[0] == target
                and raw_path[-2] == raw.get('direct_product')
                and raw_path[-1] == gather_step.item
                and isinstance(raw.get('direct_recipe'), str)
                and bool(raw['direct_recipe'])
                and gather_start.get('resource_in_current_observation') is True
                and gather_start.get('fair_target_identity_observed') is True
                and gather_start.get('travel_is_lower_bound_not_arrival_proof') is True
                and type(gather_start.get('resource_inventory_now')) is int
                and gather_start['resource_inventory_now'] >= 0
                and type(gather_start.get('target_inventory_after_this_step')) is int
                and type(gather_step.threshold) is int
                and gather_start['target_inventory_after_this_step'] ==
                    gather_step.threshold > gather_start['resource_inventory_now']
                and type(gather_parameters.get('quantity')) is int
                and gather_parameters['quantity'] == (
                    gather_step.threshold - gather_start['resource_inventory_now']))
            raw_gather_hint = (
                " This sole current raw gather has observed resource and fair-target "
                "start facts and a same-tick native-recipe path to the local target. "
                "Gathering its bounded quantity supplies a useful recipe input "
                "(level 1); the path alone does not prove an already removed "
                "production blocker (level 2) or completed downstream output. "
                "Use level 2 only with an independent current blocker fact. "
                "A contrary current fact can lower the score. Native harvest and "
                "later recipe steps still require fresh verification."
                if qualified_raw_gather else "")
            target_completion = row.get('local_target_completion_evidence')
            local_target = row.get('local_target')
            local_target_completion_hint = ""
            target_step = plan.steps[0] if len(plan.steps) == 1 else None
            if target_step is not None:
                parameters = target_step.parameters if isinstance(target_step.parameters, dict) else {}
                current_target = (primary.get('inventory_target')
                                  if isinstance(primary, dict) else None)
                target_start = row.get('craft_start_evidence')
                target_dependency = row.get('craft_dependency')
                expected_products = (target_start.get('expected_products_after_native_verification')
                                     if isinstance(target_start, dict) else None)
                expected_output = (expected_products.get(target)
                                   if isinstance(expected_products, dict) else None)
                current = (target_completion.get('inventory_now')
                           if isinstance(target_completion, dict) else None)
                shortfall = (target_completion.get('shortfall_now')
                             if isinstance(target_completion, dict) else None)
                reported_output = (target_completion.get('expected_output_after_native_receipt')
                                   if isinstance(target_completion, dict) else None)
                qualified_target_completion = (
                    target_step.action == 'factory_craft_job'
                    and target_step.effect == 'craft_job_complete'
                    and target_step.item == target
                    and isinstance(parameters.get('recipe'), str)
                    and isinstance(parameters.get('receipt'), str)
                    and bool(parameters['receipt'])
                    and type(parameters.get('batches')) is int
                    and parameters['batches'] > 0
                    and local_target == target
                    and isinstance(primary, dict)
                    and primary.get('inventory_target') == current_target
                    and type(current_target) is int and current_target > 0
                    and row.get('work_scope') == 'immediate'
                    and row.get('unknowns') == []
                    and isinstance(target_completion, dict)
                    and target_completion.get('observed_tick') == tick
                    and isinstance(facts, dict)
                    and target_completion.get('session_id') == facts.get('session_id')
                    and target_completion.get('target_item') == target
                    and target_completion.get('target_inventory') == current_target
                    and type(current) is int and current >= 0
                    and type(shortfall) is int and shortfall == max(0, current_target - current)
                    and type(expected_output) is int and expected_output > 0
                    and reported_output == expected_output
                    and type(target_completion.get('shortfall_after_expected_output')) is int
                    and target_completion.get('shortfall_after_expected_output') ==
                        max(0, shortfall - expected_output)
                    and target_completion.get(
                        'would_close_current_shortfall_if_native_receipt_verifies') is
                        (shortfall > 0 and expected_output >= shortfall)
                    and target_completion.get('native_recipe') == parameters.get('recipe')
                    and type(target_completion.get('native_batches')) is int
                    and target_completion.get('native_batches') == parameters.get('batches')
                    and target_completion.get('native_receipt_required_for_completion') is True
                    and target_completion.get('forecast_is_not_completed_output') is True
                    and target_completion.get('inventory_basis') ==
                        'coherent_snapshot_and_atomic_craft_inventory'
                    and isinstance(target_start, dict)
                    and target_start.get('observed_tick') == tick
                    and target_start.get('native_recipe') == parameters.get('recipe')
                    and target_start.get('native_receipt_required_for_completion') is True
                    and all(target_start.get(key) is True for key in (
                        'input_costs_match_native_recipe', 'inputs_in_inventory_now',
                        'recipe_unlocked_and_handcraftable', 'player_connected_and_bound',
                        'crafting_queue_empty', 'craft_job_protocol_ready'))
                    and isinstance(target_dependency, dict)
                    and target_dependency.get('observed_tick') == tick
                    and target_dependency.get('current_craft_product') == target
                    and target_dependency.get('planner_item_path') == [target]
                    and target_dependency.get('basis') ==
                        'current_recursive_planner_provenance_and_native_recipe')
                if qualified_target_completion:
                    closes = target_completion[
                        'would_close_current_shortfall_if_native_receipt_verifies']
                    if closes:
                        local_target_completion_hint = (
                            " `local_target_completion_evidence` is same-tick, session-bound "
                            "native inventory and recipe evidence. It supports level 2 only "
                            "because the current local-target shortfall would close after "
                            "the required native receipt verifies. It forecasts output and "
                            "never reports completion. Do not infer blocker removal from "
                            "future research or an unverified plan. A separate blocker or "
                            "due-starvation claim needs its own specific same-tick observed "
                            "evidence.")
                    elif shortfall > 0:
                        local_target_completion_hint = (
                            " `local_target_completion_evidence` shows a same-tick native "
                            "craft that leaves the current local-target shortfall open; score "
                            "it as partial progress, not target closure or blocker removal. "
                            "Its output still requires the native receipt. A separate blocker "
                            "or due-starvation claim needs its own specific same-tick observed "
                            "evidence.")
                    else:
                        local_target_completion_hint = (
                            " `local_target_completion_evidence` shows the observed target "
                            "is already met before this craft. Do not assign target-closure "
                            "benefit to surplus output; any level-2 blocker-removal claim still "
                            "needs separate, specific same-tick observed evidence. The craft "
                            "output itself requires its native receipt.")
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
            craft_start = row.get('craft_start_evidence')
            craft_dependency = row.get('craft_dependency')
            craft_path = (craft_dependency.get('planner_item_path')
                          if isinstance(craft_dependency, dict) else None)
            craft_step = plan.steps[0] if len(plan.steps) == 1 else None
            craft_parameters = (craft_step.parameters if craft_step is not None
                                and isinstance(craft_step.parameters, dict) else {})
            expected_craft_products = (
                craft_start.get('expected_products_after_native_verification')
                if isinstance(craft_start, dict) else None)
            qualified_intermediate_craft = (
                craft_step is not None
                and craft_step.action in {'factory_craft', 'factory_craft_job'}
                and isinstance(target, str) and bool(target)
                and isinstance(craft_step.item, str) and bool(craft_step.item)
                and craft_step.item != target
                and row.get('work_scope') == 'immediate'
                and row.get('unknowns') == []
                and type(tick) is int
                and isinstance(craft_start, dict)
                and craft_start.get('observed_tick') == tick
                and craft_start.get('native_recipe') == craft_parameters.get('recipe')
                and craft_start.get('recipe_unlocked_and_handcraftable') is True
                and isinstance(expected_craft_products, dict)
                and type(expected_craft_products.get(craft_step.item)) is int
                and expected_craft_products[craft_step.item] > 0
                and isinstance(craft_dependency, dict)
                and craft_dependency.get('observed_tick') == tick
                and craft_dependency.get('current_craft_product') == craft_step.item
                and isinstance(craft_path, list) and len(craft_path) >= 2
                and craft_path[0] == target and craft_path[-1] == craft_step.item)
            craft_hint = (
                " `craft_start_evidence` shows the current actor, queue, recipe, "
                "and carried ingredients needed to start this handcraft; "
                "`craft_dependency` traces its product along the current planner "
                "recipe path to the local target. This is level-1 partial progress "
                "from this current planner-linked intermediate craft: it does not close the "
                "local-target shortfall or establish blocker removal. The craft and "
                "later production still need fresh native receipt and precondition "
                "checks."
                if qualified_intermediate_craft else ""
            )
            shared_bill = row.get('shared_bill_craft')
            craft_start = row.get('craft_start_evidence')
            craft_step = plan.steps[0] if len(plan.steps) == 1 else None
            craft_parameters = craft_step.parameters if craft_step is not None else None
            bill_output = (craft_start.get('expected_products_after_native_verification')
                           if isinstance(craft_start, dict) else None)
            qualified_bill_start = (
                len(selected) == 1
                and craft_step is not None and craft_step.action == 'factory_craft_job'
                and isinstance(craft_step.item, str) and bool(craft_step.item)
                and isinstance(craft_parameters, dict)
                and isinstance(craft_parameters.get('receipt'), str)
                and bool(craft_parameters['receipt'])
                and isinstance(craft_parameters.get('recipe'), str)
                and bool(craft_parameters['recipe'])
                and type(craft_parameters.get('batches')) is int
                and craft_parameters['batches'] > 0
                and row.get('work_scope') == 'lookahead'
                and row.get('unknowns') == [] and row.get('reasons') == []
                and type(row.get('urgency')) is int and row['urgency'] == 0
                and row.get('research_deadline_tick') is None
                and type(tick) is int and isinstance(target, str) and bool(target)
                and isinstance(shared_bill, dict) and isinstance(craft_start, dict)
                and shared_bill.get('observed_tick') == tick
                and craft_start.get('observed_tick') == tick
                and shared_bill.get('local_target_item') == target
                and shared_bill.get('craft_item') == craft_step.item
                and shared_bill.get('basis') ==
                    'current_catalog_shared_material_bill_and_native_recipe'
                and shared_bill.get('forecast_is_not_paid_stock_or_completed_output') is True
                and shared_bill.get('background_overlap_requires_native_admission') is True
                and type(shared_bill.get('bounded_bill_inventory_target')) is int
                and type(shared_bill.get('inventory_now')) is int
                and type(shared_bill.get('unfilled_bill_units')) is int
                and shared_bill['inventory_now'] >= 0
                and shared_bill['bounded_bill_inventory_target'] - shared_bill['inventory_now']
                    == shared_bill['unfilled_bill_units'] > 0
                and type(shared_bill.get('expected_products_after_native_verification')) is int
                and shared_bill['expected_products_after_native_verification']
                    >= shared_bill['unfilled_bill_units']
                and isinstance(bill_output, dict)
                and bill_output.get(craft_step.item) ==
                    shared_bill['expected_products_after_native_verification']
                and craft_start.get('native_recipe') == craft_parameters.get('recipe')
                and all(craft_start.get(key) is True for key in (
                    'input_costs_match_native_recipe', 'inputs_in_inventory_now',
                    'recipe_unlocked_and_handcraftable', 'player_connected_and_bound',
                    'crafting_queue_empty', 'craft_job_protocol_ready',
                    'native_receipt_required_for_completion')))
            bill_craft_hint = (
                " `shared_bill_craft` ties this ready handcraft to a current "
                "bounded catalog bill shortfall. It can supply a useful forecast "
                "intermediate (level 1). Output needs native verification; "
                "background overlap is only possible after native job admission."
                if (isinstance(shared_bill, dict)
                    and shared_bill.get('observed_tick') == tick
                    and shared_bill.get('local_target_item') == target
                    and isinstance(row.get('craft_start_evidence'), dict)
                    and row.get('unknowns') == []) else ""
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
            qualified_utility_lab = _qualified_utility_lab_dependency(
                plan, row, local, tick)
            utility_lab_hint = (
                " `utility_lab_research_dependency` ties this paid placement to the "
                "current planner's immediate prerequisite for a specific enabled "
                "capability technology. It is useful prerequisite progress (score "
                "level 1) only; no native site or clearance has been observed, and "
                "this does not establish a powered lab, started research, or an "
                "unlocked assembler. The existing native search/build checks and "
                "action result and fresh role postcondition remain authoritative; "
                "a contrary current fact "
                "can lower the score."
                if qualified_utility_lab else ""
            )
            fuel = row.get('fuel_prerequisite')
            fuel_step = plan.steps[0] if len(plan.steps) == 1 else None
            fuel_path = fuel.get('planner_item_path') if isinstance(fuel, dict) else None
            gather_start = row.get('gather_start_evidence')
            gather_start = gather_start if isinstance(gather_start, dict) else {}
            qualified_established_fuel = (
                isinstance(fuel, dict) and fuel_step is not None
                and fuel_step.action == 'factory_gather'
                and isinstance(fuel_step.parameters, dict)
                and fuel_step.parameters.get('resource') == 'coal'
                and row.get('work_scope') == 'immediate' and row.get('unknowns') == []
                and type(tick) is int and fuel.get('observed_tick') == tick
                and fuel.get('basis') == 'current_planner_fuel_need_and_owned_native_burner'
                and gather_start.get('resource_in_current_observation') is True
                and gather_start.get('fair_target_identity_observed') is True
                and isinstance(target, str) and bool(target)
                and isinstance(fuel_path, list) and len(fuel_path) >= 2
                and fuel_path[0] == target
                and fuel.get('burner_role') == 'recipe:' + fuel_path[-1]
                and type(fuel.get('burner_unit')) is int and fuel['burner_unit'] > 0
                and type(fuel.get('fuel_now')) is int and 0 <= fuel['fuel_now'] < 5
                and type(fuel.get('coal_in_inventory_now')) is int
                and fuel['coal_in_inventory_now'] >= 0
                and type(fuel.get('current_required_units')) is int
                and fuel['current_required_units'] == 5 - fuel['fuel_now']
                and type(fuel.get('current_unfunded_units')) is int
                and fuel['current_unfunded_units'] == max(
                    0, fuel['current_required_units'] - fuel['coal_in_inventory_now'])
                and fuel['current_unfunded_units'] > 0
                and type(fuel.get('planned_gather_units')) is int
                and fuel['planned_gather_units'] == fuel_step.parameters.get('quantity')
                and type(fuel.get('gather_units_beyond_current_need')) is int
                and fuel['gather_units_beyond_current_need'] == (
                    fuel['planned_gather_units'] - fuel['current_unfunded_units'])
                and type(fuel.get('established_service_target')) is int
                and fuel['established_service_target'] == (
                    fuel['fuel_now'] + fuel['coal_in_inventory_now']
                    + fuel['planned_gather_units'])
                and fuel['established_service_target'] > 5
                and fuel.get('startup_target') is None)
            fuel_hint = (
                " `fuel_prerequisite` ties this bounded coal pickup to the current "
                "owned burner's startup need; later transfer and production remain unverified."
                if isinstance(fuel, dict) and fuel.get('startup_target') is not None
                else (" `fuel_prerequisite` identifies an owned established burner with "
                      f"{fuel['current_unfunded_units']} coal still needed for its current "
                      f"five-coal operating threshold. Of the planned {fuel['planned_gather_units']} "
                      f"coal, the other {fuel['gather_units_beyond_current_need']} support "
                      "the established producer's bulk refill, not an urgent blocker. "
                      "Score the evidenced bounded current need at level 1 without "
                      "treating the whole trip as urgent or claiming later output. "
                      "Another current contrary fact can lower the score. Native "
                      "transfer and production still require fresh verification."
                      if qualified_established_fuel else ""))
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
            input_step = plan.steps[0] if len(plan.steps) == 1 else None
            input_parameters = input_step.parameters if input_step is not None else None
            input_path = (recipe_input_start.get('planner_item_path')
                          if isinstance(recipe_input_start, dict) else None)
            qualified_recipe_input = (
                len(selected) == 1 and input_step is not None
                and input_step.action == 'factory_insert'
                and input_step.effect == 'transfer'
                and isinstance(input_parameters, dict)
                and isinstance(recipe_input_start, dict)
                # The current _transfer plan carries its item in parameters/costs.
                and input_step.item == ''
                and isinstance(input_parameters.get('item'), str)
                and input_parameters['item'] == recipe_input_start.get('ingredient')
                and row.get('work_scope') == 'immediate'
                and row.get('unknowns') == [] and row.get('reasons') == []
                and type(row.get('urgency')) is int and row['urgency'] == 0
                and row.get('research_deadline_tick') is None
                and row.get('requires_investment') is False
                and type(tick) is int and isinstance(target, str) and bool(target)
                and isinstance(row.get('local_target'), dict)
                and row['local_target'].get('item') == target
                and recipe_input_start.get('observed_tick') == tick
                and recipe_input_start.get('basis') ==
                    'current_planner_recipe_input_and_owned_native_machine'
                and recipe_input_start.get(
                    'native_transfer_and_later_output_require_verification') is True
                and isinstance(input_path, list) and 2 <= len(input_path) <= 32
                and all(isinstance(part, str) and bool(part) for part in input_path)
                and input_path[0] == target
                and isinstance(recipe_input_start.get('direct_native_recipe'), str)
                and recipe_input_start['direct_native_recipe']
                and input_path[-2:] == [recipe_input_start['direct_native_recipe'],
                                         input_parameters['item']]
                and recipe_input_start.get('owned_source_role') ==
                    input_parameters.get('role') == (
                        'recipe:' + recipe_input_start['direct_native_recipe'])
                and type(recipe_input_start.get('owned_source_unit')) is int
                and recipe_input_start['owned_source_unit'] > 0
                and type(recipe_input_start.get('ingredient_in_machine_now')) is int
                and recipe_input_start['ingredient_in_machine_now'] >= 0
                and type(recipe_input_start.get('ingredient_in_inventory_now')) is int
                and type(recipe_input_start.get('paid_quantity_to_transfer')) is int
                and recipe_input_start['paid_quantity_to_transfer'] > 0
                and recipe_input_start['ingredient_in_inventory_now'] >=
                    recipe_input_start['paid_quantity_to_transfer']
                and type(input_parameters.get('quantity')) is int
                and recipe_input_start['paid_quantity_to_transfer'] ==
                    input_parameters.get('quantity')
                and input_step.costs == {
                    input_parameters['item']:
                        recipe_input_start['paid_quantity_to_transfer']}
                and isinstance(input_parameters.get('receipt'), str)
                and input_parameters['receipt'] ==
                    recipe_input_start.get('planned_native_receipt_id') == (
                        f"{tick}:factory_insert:{input_parameters['role']}:"
                        f"{input_parameters['item']}"))
            input_hint = (
                " `recipe_input_transfer_start_evidence` ties this paid ingredient transfer "
                "to the current planner path, native recipe, owned machine, carried input, "
                "and planned receipt ID. This sole same-tick, bounded transfer would "
                "supply a useful recipe input if its paid receipt verifies (level 1); "
                "it does not itself prove an observed "
                "production blocker was removed (level 2) or downstream output. Use "
                "level 2 only with an independent current blocker fact. A contrary "
                "current fact can lower the score. The native transfer receipt and "
                "later output still require verification."
                if qualified_recipe_input else
                " A reported recipe-input transfer witness alone does not establish "
                "a current paid transfer or downstream output. Check its recipe path, "
                "owned machine, carried input, and planned receipt before assigning benefit."
                if isinstance(recipe_input_start, dict) else ""
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
                    + raw_gather_hint + craft_hint + bill_craft_hint + place_hint + fuel_hint
                    + utility_lab_hint + transfer_hint + input_hint
                    + pickup_hint + local_target_completion_hint
                ),
                "criteria": ([
                    "No demonstrated contribution to the bounded production objective",
                    "Makes useful partial progress through useful inputs, a current "
                    "planner-linked intermediate craft, an immediate planner-linked "
                    "research prerequisite backed by same-tick evidence, or evidenced "
                    "bounded capacity, "
                    "but does not establish receipt-conditional closure of an observed "
                    "local-target shortfall and does not remove a separately evidenced "
                    "current blocker or due starvation",
                    "Same-tick qualified evidence shows the action would close the current "
                    "local-target shortfall only after its native receipt verifies, or separate "
                    "same-tick evidence shows it directly removes a specific observed blocker "
                    "or due starvation",
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
                    + (" This current bill-linked handcraft has a complete, same-tick "
                       "catalog shortfall and native actor, queue, recipe, carried-input, "
                       "and receipt-protocol start evidence. No required start fact is "
                       "missing from those witnesses; identify a specific contrary "
                       "current fact before marking observation needed. Future output "
                       "still needs a native receipt and fresh verification."
                       if qualified_bill_start else "")
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
                    + (" For this utility lab, current carried stock, role absence, "
                       "actor readiness, idle research state, and the named capability "
                       "technology dependency are observed. Site clearance and travel "
                       "remain unknown; the existing bounded native placement action "
                       "resolves them and checks again before building. Do not claim "
                       "a site, arrival, power, or research result."
                       if qualified_utility_lab else "")
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
