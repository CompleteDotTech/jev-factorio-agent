"""Read-only reconciliation of bounded kit funding in retained gameplay logs.

This checks consistency, not authenticity or native acceptance.  Checkpoints own
funding; telemetry cannot release it or authorize construction.  A record stores
one detached state plus bounded transition proofs in the existing history ring.
Legacy records without this contract are usable only with the policy disabled.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from dataclasses import asdict
from collections import Counter

from .planning import solid_funding
from .telemetry import fingerprint, validate_attempt
from .skills import Step
from .state import GameSnapshot

SCHEMA = 1
KINDS = frozenset({'solid_kit_committed', 'solid_kit_abandoned', 'solid_kit_paid_handoff'})
REASONS = frozenset({'kit_deadline', 'kit_failure_budget', 'kit_action_budget',
                     'kit_endpoint_or_layout_changed', 'kit_catalog_changed',
                     'kit_demand_or_payback_changed', 'kit_evidence_unavailable'})


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _same(left: object, right: object) -> bool:
    # Type-sensitive: Python's True == 1 is not a schema match.
    return _encoded(left) == _encoded(right)


def _identity(state: dict) -> dict:
    return {k: v for k, v in state.items() if k != 'actions'}


def _tick(value: object) -> bool:
    return type(value) is int and 0 <= value <= 2**53 - 1


def _budget(values: object, key: str) -> int | None:
    value = values.get(key, 0) if isinstance(values, dict) else None
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _funding_events(history: object) -> list[dict]:
    if (not isinstance(history, list) or len(history) > 64
            or any(not isinstance(event, dict) for event in history)):
        raise ValueError('Invalid bounded funding history')
    return [event for event in history if isinstance(event, dict)
            and isinstance(event.get('kind'), str)
            and event['kind'].startswith('solid_kit_')]


def _validate_transition(event: dict, minimum: int, maximum: int, intents: list,
                         *, new: bool = False) -> None:
    kind = event['kind']
    fields = {'kind', 'key', 'tick', 'funding'} | ({'reason'} if kind == 'solid_kit_abandoned' else set())
    if kind == 'solid_kit_committed' and (new or 'step' in event):
        fields.add('step')
        _kit_step(event['step'])
    if kind == 'solid_kit_committed' and (new or 'acquisition' in event):
        fields.add('acquisition')
    if (kind not in KINDS or set(event) != fields or not _tick(event.get('tick'))
            or not minimum <= event['tick'] <= maximum):
        raise ValueError('Invalid funding transition schema')
    solid_funding.validate_state(event['funding'], event['tick'], intents)
    proof = event['funding']
    if kind == 'solid_kit_committed' and proof['actions'] == 1 and proof['started_tick'] != event['tick']:
        raise ValueError('First funding commit must establish its own start tick')
    if kind == 'solid_kit_abandoned' and (
            event.get('reason') == 'kit_deadline' and event['tick'] < proof['deadline_tick']
            or event.get('reason') == 'kit_action_budget' and proof['actions'] != solid_funding.MAX_ACTIONS):
        raise ValueError('Abandonment contradicts its intrinsic budget proof')
    key = event['funding']['key'] + ('' if kind == 'solid_kit_paid_handoff' else ':kit')
    if event['key'] != key or kind == 'solid_kit_abandoned' and event['reason'] not in REASONS:
        raise ValueError('Invalid funding transition identity')


def _kit_step(value: dict) -> Step:
    if not isinstance(value, dict) or set(value) != set(Step.__dataclass_fields__):
        raise ValueError('Invalid captured kit step')
    step = Step(**value)
    if (step.action, step.effect) not in {('factory_craft', 'inventory'), ('factory_extract', 'transfer')}:
        raise ValueError('Invalid kit action or effect')
    return step


def _acquisition_matches(commit: dict, record: dict, proof: dict, budgets: dict,
                         reservations: dict, outcomes: list, bindings: dict) -> bool:
    from .planning.catalog import Catalog
    from .planning import solid_investment
    from . import solid_routes
    value = commit.get('acquisition')
    if not isinstance(value, dict) or set(value) != {'catalog', 'reserved', 'technologies', 'recipes', 'stack_sizes'}:
        return False
    data = value['catalog']
    if (not isinstance(data, dict) or set(data) != {'recipes', 'hand_categories', 'version'}
            or len(data['recipes']) > solid_funding.MAX_EXPANSIONS):
        return False
    binding = bindings.get(proof['key'])
    if (not isinstance(binding, dict) or binding.get('catalog_sha256') != solid_funding.digest(data)
            or binding.get('version') != data['version']
            or binding.get('acquisition_sha256') != solid_funding.digest({k: v for k, v in value.items() if k != 'reserved'})):
        return False
    runtime = record['state'].get('factory', {}).get('acceptance_runtime', {})
    if runtime and runtime.get('mods', {}).get('base') != data['version']:
        return False
    if set(value['recipes']) & set(data['recipes']) or len(value['recipes']) + len(data['recipes']) > 512:
        return False
    catalog = Catalog.from_dict({**data, 'recipes': {**data['recipes'], **value['recipes']},
        'technologies': value['technologies'], 'machines': {}, 'stack_sizes': value['stack_sizes']})
    snapshot = GameSnapshot(**record['state'])
    row = snapshot.factory['solid_routes']['routes'][proof['route']]
    if solid_funding.catalog_digest(row, snapshot, catalog) != proof['catalog_sha256']:
        return False
    reserved = Counter()
    for held in reservations.values(): reserved.update(held)
    for route in solid_routes.routes(snapshot).values():
        if route.get('parts'): reserved.update(solid_routes.remaining(route))
    if not _same(dict(reserved), value['reserved']):
        return False
    if (snapshot.factory.get('crafting_queue', 0)
            or any(route['pending'] or route['state'] == 'building' and route['target']['inventory'] == 'input'
                   for route in solid_routes.routes(snapshot).values())):
        return False
    demand, _ = solid_investment.requirements(snapshot, catalog, reserved=dict(reserved))
    offer = solid_investment.offer_value(row, snapshot, catalog, demand, outcomes)
    if not offer.get('eligible'):
        return False
    expected, _ = solid_investment._kit_offer(row, snapshot, catalog, offer,
                                             reserved=dict(reserved), failures=budgets)
    return expected is not None and _same(asdict(expected.steps[0]), commit['step'])


def _project_budget_open(proof: dict, budgets: dict, step_value: dict) -> bool:
    from .solid_routes import MAX_BELTS
    parts = ['kit', 'receive', 'send', *[f'belt:{index}' for index in range(1, MAX_BELTS + 1)]]
    counts = [_budget(budgets, proof['key'] + ':' + part) for part in parts]
    if any(count is None or count >= 2 for count in counts):
        return False
    step = _kit_step(step_value)
    parameters = step.parameters or {}
    ordinary = 'factory:' + step.action + ':' + parameters.get('role', parameters.get('recipe', ''))
    count = _budget(budgets, ordinary)
    return count is not None and counts[0] + count < 2


def _native_bound(state: dict, record: dict, *, paid: bool = False,
                  observation: str = 'after_state') -> bool:
    factory = record.get(observation, {}).get('factory', {})
    row = factory.get('solid_routes', {}).get('routes', {}).get(state['route'])
    if not isinstance(row, dict) or not solid_funding.bound(state, row):
        return False
    # The parent analyzer separately validates the full paid prefix, native
    # receipts and exact initial/final controller commitments.
    if paid:
        return row.get('state') in {'building', 'ready'} and bool(row.get('parts'))
    return row.get('state') == 'proposed'


def _new_history(previous: list, current: list) -> list:
    # Compare occurrences, not value hashes: two genuine commits at the same
    # game tick may have identical plan events but different funding proofs.
    overlap = next((size for size in range(min(len(previous), len(current)), 0, -1)
                    if _same(previous[-size:], current[:size])), 0)
    if len(current) > 8 or overlap < len(previous) and len(current) < 8:
        raise ValueError('History discontinuity cannot be explained by ring truncation')
    return current[overlap:]


def _selection_matches(record: dict, plan_id: str, expected_source=None) -> bool:
    decision = record.get('decision')
    fields = {'plan_id', 'source', 'reason', 'state', 'questions', 'answers', 'utilities',
              'model_called', 'diagnostics'}
    if (not isinstance(plan_id, str) or not plan_id
            or not isinstance(decision, dict) or set(decision) != fields
            or decision.get('plan_id') != plan_id
            or not isinstance(decision.get('reason'), str) or type(decision.get('model_called')) is not bool
            or any(not isinstance(decision.get(key), dict) for key in
                   ('state', 'questions', 'answers', 'utilities', 'diagnostics'))):
        return False
    source, called, policy = decision['source'], decision['model_called'], record.get('policy')
    if record.get('model_call') is not called or expected_source is not None and source != expected_source:
        return False
    if not ((policy == 'deterministic' and source == 'deterministic' and not called)
            or policy in {'jev', 'hybrid'} and source in {'jev', 'mock'} and called
            or policy == 'hybrid' and (source == 'deterministic-singleton' and not called
                                      or source == 'deterministic-fallback')):
        return False
    return True


def _kit_plan_observed(proof: dict, record: dict, tick: int, fresh: list,
                       seen_attempts: set[str], budgets: dict, reservations: dict,
                       outcomes: list, bindings: dict) -> bool:
    action = record.get('action')
    compatible = action in {'factory_craft', 'factory_extract'} or (
        action == 'observe' and record.get('verified') is False) or (
        action == 'verify' and record.get('verified') is True)
    decision = record.get('decision')
    if not _selection_matches(record, proof['key'] + ':kit'):
        return False
    commit = next((value for value in fresh if value.get('kind') == 'solid_kit_committed'
                   and _same(value.get('funding'), proof) and value.get('tick') == tick), {})
    step_value = commit.get('step')
    step = _kit_step(step_value)
    if not _acquisition_matches(commit, record, proof, budgets, reservations, outcomes, bindings) or step.satisfied(GameSnapshot(**record['state'])) or (
            action == 'verify' and not step.satisfied(GameSnapshot(**record['after_state']))):
        return False
    if action in {'factory_craft', 'factory_extract'}:
        verified = record.get('verified') is True
        values = record.get('attempt_outcomes', []) if verified else [record.get('attempt')]
        candidates = [value for value in values if isinstance(value, dict)
            and value.get('plan_id') == proof['key'] + ':kit' and value.get('action') == action
            and value.get('id') not in seen_attempts and value.get('origin') == 'new'
            and value.get('process_id') == record.get('process_id')
            and _tick(value.get('started_tick')) and tick <= value['started_tick'] <= record['after_state']['tick']]
        if len(candidates) != 1:
            return False
        attempt = candidates[0]
        validate_attempt(attempt, finished=verified)
        if step is not None and (step.action != action or attempt['step_index'] != 0
                or fingerprint(step_value) != attempt['step_sha256']
                or not _attempt_endpoint(attempt, step_value, record['state'])
                or step.satisfied(GameSnapshot(**record['state']))
                or verified and not step.satisfied(GameSnapshot(**record['after_state']))):
            return False
        dispatch = attempt['dispatch_phases'].get('dispatch', {})
        if verified:
            if (attempt.get('outcome') != 'verified' or attempt.get('finished_tick') != record['after_state']['tick']
                    or record.get('pending') is not None or record.get('attempt') is not None
                    or dispatch.get('status') != 'returned'):
                return False
        elif (not isinstance(record.get('pending'), dict) or record['pending'].get('action') != action
              or record['pending'].get('started_tick') != attempt['started_tick']
              or record['pending'].get('dispatch') not in {'returned', 'ambiguous'}
              or dispatch.get('status') != ('returned' if record['pending']['dispatch'] == 'returned' else 'failed')):
            return False
    plans = [(index, event) for index, event in enumerate(fresh)
             if event.get('kind') == 'plan_committed']
    return compatible and len(plans) == 1 and any(
        set(event) == {'kind', 'plan', 'source', 'tick'}
        and event.get('kind') == 'plan_committed' and event.get('plan') == proof['key'] + ':kit'
        and event.get('source') in {'deterministic', 'deterministic-singleton',
                                     'deterministic-fallback', 'jev', 'mock'}
        and event['source'] == decision['source']
        and _tick(event.get('tick')) and event['tick'] == tick
        and any(value.get('kind') == 'solid_kit_committed' and _same(value.get('funding'), proof)
                and value.get('tick') == tick for value in fresh[:index])
        for index, event in plans)


def _missing_kit(proof: dict, record: dict, observation: str) -> bool:
    from .solid_routes import remaining
    if not _native_bound(proof, record, observation=observation):
        return False
    snapshot = record[observation]
    row = snapshot['factory']['solid_routes']['routes'][proof['route']]
    return any(snapshot.get('inventory', {}).get(item, 0) < count for item, count in remaining(row).items())


def _observable_trigger(proof: dict, reason: str, tick: int, record: dict) -> bool:
    if reason == 'kit_deadline':
        return tick >= proof['deadline_tick']
    observations = [name for name in ('state', 'after_state')
                    if record.get(name, {}).get('tick') == tick]
    if reason == 'kit_endpoint_or_layout_changed':
        return any(not (_native_bound(proof, record, observation=name)
                        or _native_bound(proof, record, paid=True, observation=name))
                   for name in observations)
    if reason == 'kit_action_budget' and proof['actions'] == solid_funding.MAX_ACTIONS:
        return any(_missing_kit(proof, record, name) for name in observations)
    # Catalog/payback recomputation and intermediate observations are not in
    # retained records. An exhausted count written by that observer is not
    # independent evidence of the trigger. Mark that window unmeasurable.
    return False


def _failed_precondition(step_value: dict | None, record: dict) -> bool:
    return (step_value is not None and not _kit_step(step_value).satisfied(GameSnapshot(**record['after_state']))
            and not _kit_step(step_value).allowed(GameSnapshot(**record['after_state'])))


def _abandonment_count(proof: dict, event: dict, record: dict,
                       prior_budget: int | None, fresh: list, active_kit: bool,
                       step_value: dict | None) -> int | None:
    if prior_budget is None:
        return None
    reason, tick = event['reason'], event['tick']
    if reason == 'kit_action_budget' and active_kit:
        return None
    preceding = fresh[:next((index for index, value in enumerate(fresh) if _same(value, event)), 0)]
    failures = [value for value in preceding if value.get('kind') == 'plan_failed'
                and value.get('plan') == proof['key'] + ':kit']
    failed = (active_kit and len(failures) == 1 and set(failures[0]) == {'kind', 'plan', 'reason', 'tick'}
              and isinstance(failures[0]['reason'], str) and bool(failures[0]['reason'])
              and _tick(failures[0]['tick']) and failures[0]['tick'] == tick
              and record.get('action') == 'observe' and record.get('verified') is False
              and (any(_observable_trigger(proof, cause, tick, record) for cause in
                       ('kit_deadline', 'kit_endpoint_or_layout_changed'))
                   or tick == record['after_state']['tick'] and _failed_precondition(step_value, record)))
    if reason != 'kit_failure_budget':
        return max(2, prior_budget) + int(failed) if _observable_trigger(proof, reason, tick, record) else None
    if failed:
        observer_floor = any(_observable_trigger(proof, cause, tick, record) for cause in
                             ('kit_deadline', 'kit_endpoint_or_layout_changed'))
        return (max(2, prior_budget) if observer_floor else prior_budget) + 1
    return prior_budget if prior_budget >= 2 and not failures else None


def _paid_growth_observed(before: dict, after: dict, proof: dict, attempts: list, now: int) -> bool:
    old, new = before.get('parts', {}), after.get('parts', {})
    added = {part: value for part, value in new.items() if part not in old}
    if not added:
        return True
    if len(added) != 1:
        return False
    part, paid = next(iter(added.items()))
    for attempt in attempts:
        if (isinstance(attempt, dict) and attempt.get('action') == 'factory_solid_build'
                and attempt.get('plan_id') == proof['key'] + ':' + part
                and attempt.get('receipt') == paid.get('receipt')
                and _tick(attempt.get('started_tick')) and attempt['started_tick'] <= now):
            validate_attempt(attempt, finished='outcome' in attempt)
            component = next((value for value in after.get('steps', []) if value.get('part') == part), None)
            if component is None:
                return False
            expected = Step('factory_solid_build', 'solid_component', costs={component['name']: 1},
                parameters={'route': proof['route'], 'layout': proof['layout'], 'part': part,
                            'receipt': paid['receipt']}, timeout_ticks=1800)
            return attempt['step_index'] == 0 and attempt['step_sha256'] == fingerprint(asdict(expected))
    return False


def _current_dispatch_attempts(record: dict, before: int, now: int, seen: set[str]) -> list:
    verified = record.get('verified') is True
    values = record.get('attempt_outcomes', []) if verified else [record.get('attempt')]
    candidates = []
    for value in values:
        if (not isinstance(value, dict) or value.get('action') != record.get('action')
                or value.get('id') in seen or value.get('origin') != 'new'
                or value.get('process_id') != record.get('process_id')
                or not _tick(value.get('started_tick')) or not before <= value['started_tick'] <= now):
            continue
        validate_attempt(value, finished=verified)
        dispatch = value['dispatch_phases'].get('dispatch', {})
        if verified:
            if (value['outcome'] != 'verified' or value['finished_tick'] != now
                    or record.get('pending') is not None or record.get('attempt') is not None
                    or dispatch.get('status') != 'returned'):
                continue
        else:
            pending = record.get('pending')
            if (not isinstance(pending, dict) or pending.get('action') != record.get('action')
                    or pending.get('started_tick') != value['started_tick']
                    or pending.get('dispatch') not in {'returned', 'ambiguous'}
                    or dispatch.get('status') != ('returned' if pending['dispatch'] == 'returned' else 'failed')):
                continue
        candidates.append(value)
    return candidates if len(candidates) == 1 else []


def _attempt_endpoint(attempt: dict, step: dict, state: dict) -> bool:
    parameters = step.get('parameters') or {}
    unit = state.get('factory', {}).get('entities', {}).get(parameters.get('role'), {}).get('unit_number')
    unit = unit if type(unit) is int and unit > 0 else None
    return (_same(attempt.get('receipt'), parameters.get('receipt'))
            and _same(attempt.get('expected_unit_number'), unit))


def _completed_dispatches(record, previous_pending, previous_attempt, seen):
    """A completion belongs to this dispatch or the exact retained pending RPC."""
    if record.get('verified') is not True or record.get('pending') is not None or record.get('attempt') is not None:
        return []
    if previous_pending is None:
        return _current_dispatch_attempts(record, record['state']['tick'], record['after_state']['tick'], seen)
    if (not isinstance(previous_attempt, dict)
            or previous_pending.get('action') != previous_attempt.get('action')
            or previous_pending.get('started_tick') != previous_attempt.get('started_tick')
            or record.get('action') not in {'verify', previous_attempt.get('action')}):
        return []
    values = [value for value in record.get('attempt_outcomes', []) if isinstance(value, dict)
        and value.get('outcome') == 'verified' and value.get('finished_tick') == record['after_state']['tick']
        and all(_same(value.get(field), previous_attempt.get(field)) for field in
                ('id', 'origin', 'process_id', 'action', 'plan_id', 'step_index', 'step_sha256',
                 'started_tick', 'expected_unit_number', 'receipt'))]
    for value in values:
        validate_attempt(value, finished=True)
        if value['dispatch_phases'].get('dispatch', {}).get('status') not in {'returned', 'failed'}:
            return []
    return values if len(values) == 1 else []


def _dispatch_crossed_deadline(record: dict, before: int, deadline: int, now: int,
                               seen: set[str]) -> bool:
    # Any ordinary controller action may run while optional funding is held;
    # infrastructure does not replace the science/maintenance frontier. Mere
    # action labels, or old outcomes repeated in the ring, do not prove dispatch.
    action = record.get('action')
    if not isinstance(action, str) or action in {'observe', 'verify'}:
        return False
    candidates = _current_dispatch_attempts(record, before, now, seen)
    decision = record.get('decision')
    return (len(candidates) == 1 and candidates[0]['started_tick'] < deadline
            and isinstance(decision, dict) and candidates[0]['plan_id'] == decision.get('plan_id'))


def _initial_history_state(events: list[dict], working: dict | None, budgets: dict) -> bool:
    # A bounded ring may begin mid-project. Its first proof anchors that
    # suffix, but all following transitions and its final ownership must agree.
    state = None
    last_tick = -1
    abandoned = set()
    for index, event in enumerate(events):
        proof = event['funding']
        if event['tick'] < last_tick:
            return False
        last_tick = event['tick']
        if event['kind'] == 'solid_kit_committed':
            if proof['key'] in abandoned:
                return False
            if index and (state is None and proof['actions'] != 1 or state is not None and (
                    not _same(_identity(state), _identity(proof)) or proof['actions'] != state['actions'] + 1)):
                return False
            state = proof
        else:
            if event['kind'] == 'solid_kit_abandoned':
                count = _budget(budgets, proof['key'] + ':kit')
                if count is None or count < 2:
                    return False
                abandoned.add(proof['key'])
            if index and not _same(state, proof):
                return False
            state = None
    return not events or _same(state, working)


def _kit_clear_observed(record, proof, step_value, previous_pending, previous_attempt, seen):
    if record.get('verified') is not True or not _kit_step(step_value).satisfied(GameSnapshot(**record['after_state'])):
        return False
    key = proof['key'] + ':kit'
    now = record['after_state']['tick']
    if record.get('action') == 'verify' and previous_pending is None:
        decision = record.get('decision')
        return (record.get('pending') is None and record.get('attempt') is None
                and (decision is None or isinstance(decision, dict) and decision.get('plan_id') == key))
    values = _completed_dispatches(record, previous_pending, previous_attempt, seen)
    return (len(values) == 1 and values[0].get('plan_id') == key and values[0].get('step_index') == 0
            and values[0].get('step_sha256') == fingerprint(step_value)
            and _attempt_endpoint(values[0], step_value, record['state']))


def funding_history_issues(initial: dict, rows: list[dict], final: dict) -> list[str]:
    """Check retained kit ownership and every observed transition, without I/O.

    Only fixed issue labels escape this function.  Never emit keys, paths, raw
    records or arbitrary exception text.  Missing funding evidence is unknown,
    not permission to silently clear or invent an optional-capital lock.
    """
    issues: set[str] = set()
    try:
        policy = initial.get('solid_science_policy') is True
        enabled = policy or initial.get('solid_funding') is not None or final.get('solid_funding') is not None
        enabled = enabled or any('solid_funding' in row or 'solid_funding_schema' in row
                                 or _funding_events(row.get('history', [])) for row in rows)
        if not enabled:
            return []
        if policy and any('solid_funding' not in checkpoint for checkpoint in (initial, final)):
            issues.add('solid_funding_checkpoint_field_missing')
        if policy and 'pending' not in initial:
            issues.add('solid_funding_pending_evidence_missing')
        if not rows:
            return ['solid_funding_records_missing']
        intents = initial.get('solid_intents', [])
        previous_tick = initial.get('last_tick')
        if not _tick(previous_tick):
            return ['solid_funding_initial_tick_invalid']
        working = deepcopy(initial.get('solid_funding'))
        bindings = deepcopy(initial.get('solid_funding_catalogs', {}))
        solid_funding.validate_catalog_declarations(bindings, previous_tick)
        final_bindings = final.get('solid_funding_catalogs', {})
        solid_funding.validate_catalog_declarations(final_bindings, final['last_tick'])
        if any(not _same(value, final_bindings.get(key)) for key, value in bindings.items()):
            issues.add('solid_funding_final_catalog_declaration_mismatch')
        if any(key not in bindings for key in final_bindings):
            # Retained observations do not carry an independently anchored
            # runtime catalog from which to derive a new declaration. A new
            # project requires a separately captured baseline, not a final
            # checkpoint that declares its own future acquisition authority.
            issues.add('solid_funding_new_catalog_declaration_unproven')
        if working is not None:
            solid_funding.validate_state(working, previous_tick, intents)
        # The ring repeats old events. Seed it from the initial checkpoint and
        # process new event values only once. Hash storage is bounded by input
        # record count (already bounded by the parent analyzer's byte limits).
        seen = set()
        initial_events = _funding_events(initial.get('history', []))
        for event in initial_events:
            _validate_transition(event, 0, previous_tick, intents)
            seen.add(hashlib.sha256(_encoded(event)).digest())
        if not _initial_history_state(initial_events, working, initial.get('failures', {})):
            issues.add('solid_funding_initial_history_mismatch')
        previous_budgets = initial.get('failures', {})
        previous_pending = initial.get('pending')
        previous_attempt = initial.get('attempt')
        previous_routes = initial.get('solid_commitments', {})
        previous_history = initial.get('history', [])
        reservations = deepcopy(initial.get('reservations', {}))
        outcomes = deepcopy(initial.get('attempt_outcomes', []))
        ordinary_plan = deepcopy(initial.get('active_plan'))
        ordinary_index = initial.get('step_index', 0)
        seen_attempts = {value['id'] for value in initial.get('attempt_outcomes', [])
                         if isinstance(value, dict) and isinstance(value.get('id'), str)}
        if isinstance(previous_attempt, dict) and isinstance(previous_attempt.get('id'), str):
            seen_attempts.add(previous_attempt['id'])
        active_kit = (working is not None and isinstance(initial.get('active_plan'), dict)
                      and initial['active_plan'].get('id') == working['key'] + ':kit')
        active_step = initial['active_plan']['steps'][0] if active_kit else None
        if active_kit:
            ordinary_plan = None
        if any(value.get('kind') == 'plan_committed' and (
                not _tick(value.get('tick')) or value['tick'] > previous_tick)
               for value in previous_history):
            issues.add('solid_funding_plan_history_invalid')
        for record in rows:
            if policy and 'pending' not in record:
                issues.add('solid_funding_pending_evidence_missing')
            now = record.get('after_state', {}).get('tick')
            if not _tick(now) or now < previous_tick:
                issues.add('solid_funding_record_tick_invalid')
                continue
            if type(record.get('solid_funding_schema')) is not int or record.get('solid_funding_schema') != SCHEMA:
                issues.add('solid_funding_schema_missing_or_invalid')
            if 'solid_funding' not in record:
                issues.add('solid_funding_record_missing')
            current = record.get('solid_funding')
            if current is not None:
                try:
                    solid_funding.validate_state(current, now, intents)
                except (ValueError, TypeError, KeyError, AttributeError):
                    issues.add('solid_funding_record_invalid')
                    continue
            current_budgets = record.get('failure_budgets', {})
            before_tick = record.get('state', {}).get('tick')
            history = record.get('history', [])
            _funding_events(history)  # Validate before inspecting common events.
            fresh = _new_history(previous_history, history)
            selected_kits = {value.get('key') for value in fresh if value.get('kind') == 'solid_kit_committed'}
            ordinary_commits = [value for value in fresh if value.get('kind') == 'plan_committed'
                                and value.get('plan') not in selected_kits]
            if ordinary_commits:
                if ordinary_plan is not None or active_kit or previous_pending is not None or len(ordinary_commits) != 1:
                    issues.add('solid_funding_ordinary_plan_replaced')
                event = ordinary_commits[0]
                if (set(event) != {'kind', 'plan', 'source', 'tick', 'definition'}
                        or not isinstance(event.get('source'), str)
                        or not _tick(event.get('tick')) or event['tick'] != before_tick
                        or not _selection_matches(record, event.get('plan'), event.get('source'))):
                    issues.add('solid_funding_ordinary_selection_unproven')
                ordinary_plan = deepcopy(event.get('definition'))
                ordinary_index = 0
                if not isinstance(ordinary_plan, dict) or ordinary_plan.get('id') != event.get('plan'):
                    issues.add('solid_funding_ordinary_plan_definition_missing')
                    ordinary_plan = {'id': event.get('plan')}
            if any(value.get('kind') == 'plan_committed' and (
                    not _tick(value.get('tick')) or value['tick'] > now) for value in history):
                issues.add('solid_funding_plan_history_invalid')
            prior = deepcopy(working)
            action_budget_due = (prior is not None and prior['actions'] == solid_funding.MAX_ACTIONS
                                 and previous_pending is None and not active_kit
                                 and _missing_kit(prior, record, 'state'))
            prior_unbound = (prior is not None and previous_pending is None
                             and not (_native_bound(prior, record, observation='state')
                                      or _native_bound(prior, record, paid=True, observation='state')))
            unbound_released = False
            handoff_due = (working is not None and previous_pending is None
                           and _native_bound(working, record, paid=True, observation='state'))
            handed_off = False
            new_commits = 0
            committed_proof = None
            abandoned_keys: set[str] = set()
            for event in _funding_events(record.get('history', [])):
                event_digest = hashlib.sha256(_encoded(event)).digest()
                if event_digest in seen:
                    continue
                seen.add(event_digest)
                kind = event['kind']
                proof = event.get('funding')
                event_tick = event.get('tick')
                try:
                    _validate_transition(event, previous_tick, now, intents, new=True)
                except (ValueError, TypeError, KeyError, AttributeError):
                    issues.add('solid_funding_transition_invalid')
                    continue
                if kind == 'solid_kit_committed':
                    # A controller step selects at most one new kit plan. Old
                    # ring entries were skipped above and do not use this slot.
                    new_commits += 1
                    if new_commits > 1:
                        issues.add('solid_funding_multiple_commits_in_record')
                        continue
                    starting = working is None
                    prior_budget = _budget(previous_budgets, proof['key'] + ':kit')
                    if (starting and (proof['actions'] != 1 or proof['started_tick'] != event_tick
                                      or proof['deadline_tick'] != event_tick + solid_funding.MAX_TICKS)
                            or not starting and (not _same(_identity(working), _identity(proof))
                                                 or proof['actions'] != working['actions'] + 1)
                            or prior_budget is None or prior_budget >= 2
                            or not _project_budget_open(proof, previous_budgets, event['step'])
                            or previous_pending is not None
                            or active_kit
                            or ordinary_plan is not None
                            or proof['key'] in abandoned_keys
                            or not _tick(before_tick) or event_tick != before_tick
                            or event_tick >= proof['deadline_tick']
                            or not _kit_plan_observed(proof, record, before_tick, fresh, seen_attempts,
                                                     previous_budgets, reservations, outcomes, bindings)
                            or not _native_bound(proof, record, observation='state')):
                        issues.add('solid_funding_commit_not_reconciled')
                    else:
                        working = deepcopy(proof)
                        committed_proof = proof
                        active_kit = True
                        active_step = event['step']
                else:
                    if working is None or not _same(working, proof):
                        issues.add('solid_funding_release_proof_mismatch')
                        continue
                    if kind == 'solid_kit_abandoned':
                        count = _budget(current_budgets, proof['key'] + ':kit')
                        reason = event.get('reason')
                        if (count is None or count < 2 or not isinstance(reason, str)
                                or not 0 < len(reason) <= 128 or not _tick(before_tick)
                                or event_tick < before_tick):
                            issues.add('solid_funding_abandonment_not_reconciled')
                            continue
                        expected_count = _abandonment_count(proof, event, record,
                                _budget(previous_budgets, proof['key'] + ':kit'), fresh, active_kit, active_step)
                        if expected_count is None:
                            issues.add('solid_funding_abandonment_trigger_unproven')
                        elif count != expected_count:
                            issues.add('solid_funding_abandonment_budget_mismatch')
                        # Later events cannot reuse the pre-record budget after
                        # this transition established that the project is spent.
                        abandoned_keys.add(proof['key'])
                        if prior_unbound and _same(prior, proof) and event_tick == before_tick:
                            unbound_released = True
                    else:
                        if (new_commits or previous_pending is not None
                                or not _tick(before_tick) or event_tick != before_tick
                                or not _native_bound(proof, record, paid=True, observation='state')):
                            issues.add('solid_funding_paid_handoff_not_observed')
                            continue
                        handed_off = True
                    working = None
            if handoff_due and not handed_off:
                issues.add('solid_funding_paid_handoff_missing')
            if prior_unbound and not unbound_released:
                issues.add('solid_funding_initial_proposal_not_reconciled')
            if not _same(working, current):
                issues.add('solid_funding_record_history_mismatch')
            payment_proofs = {_encoded(_identity(value)): value for value in (prior, working, current, committed_proof)
                              if value is not None}
            for payment_proof in payment_proofs.values():
                route = payment_proof['route']
                before_routes = record.get('state', {}).get('factory', {}).get('solid_routes', {}).get('routes', {})
                after_routes = record.get('after_state', {}).get('factory', {}).get('solid_routes', {}).get('routes', {})
                carried = []
                if (isinstance(previous_pending, dict) and isinstance(previous_attempt, dict)
                        and previous_pending.get('action') == previous_attempt.get('action') == 'factory_solid_build'
                        and previous_pending.get('started_tick') == previous_attempt.get('started_tick')):
                    validate_attempt(previous_attempt)
                    carried = [previous_attempt]
                if not _paid_growth_observed(previous_routes.get(route, {}), before_routes.get(route, {}),
                                             payment_proof, carried, before_tick):
                    issues.add('solid_funding_paid_prefix_without_dispatch')
                dispatched = list(carried)
                if record.get('action') == 'factory_solid_build':
                    current_attempts = _current_dispatch_attempts(record, before_tick, now, seen_attempts)
                    if not current_attempts:
                        issues.add('solid_funding_paid_dispatch_not_reconciled')
                    dispatched.extend(current_attempts)
                if not _paid_growth_observed(before_routes.get(route, {}), after_routes.get(route, {}),
                                             payment_proof, dispatched, now):
                    issues.add('solid_funding_paid_prefix_without_dispatch')
            # A receipt may clear pending only after the observer's funding
            # reconciliation was deferred. Retaining the lock across that
            # boundary is safe; payment alone must not silently release it.
            if current is not None and not (_native_bound(current, record)
                                            or _native_bound(current, record, paid=True)):
                issues.add('solid_funding_owned_proposal_not_observed')
            if current is not None:
                budget = _budget(current_budgets, current['key'] + ':kit')
                prior_budget = _budget(previous_budgets, current['key'] + ':kit')
                failures = [value for value in fresh if value.get('kind') == 'plan_failed'
                            and value.get('plan') == current['key'] + ':kit']
                failed_once = (active_kit and prior_budget == 0 and budget == 1
                    and len(failures) == 1 and set(failures[0]) == {'kind', 'plan', 'reason', 'tick'}
                    and isinstance(failures[0]['reason'], str) and bool(failures[0]['reason'])
                    and _tick(failures[0]['tick']) and failures[0]['tick'] == now
                    and record.get('action') == 'observe' and record.get('verified') is False
                    and previous_pending is None and record.get('pending') is None
                    and record.get('attempt') is None and active_step is not None
                    and _failed_precondition(active_step, record))
                if budget is None or budget >= 2:
                    issues.add('solid_funding_retained_budget_exhausted')
                if budget != prior_budget and not failed_once:
                    issues.add('solid_funding_retained_budget_change_unproven')
                if failures and not failed_once:
                    issues.add('solid_funding_failed_plan_not_reconciled')
                if failed_once:
                    active_kit = False
                if action_budget_due:
                    issues.add('solid_funding_action_budget_release_missing')
                if (previous_pending is None and _tick(before_tick)
                        and (before_tick >= current['deadline_tick']
                             or now >= current['deadline_tick'] and not _dispatch_crossed_deadline(
                                 record, before_tick, current['deadline_tick'], now, seen_attempts))):
                    issues.add('solid_funding_expired_state_retained')
            if not policy and (current is not None or working is not None
                               or _funding_events(record.get('history', []))):
                issues.add('solid_funding_disabled_policy_has_state')
            completed = _completed_dispatches(record, previous_pending, previous_attempt, seen_attempts)
            service_completed = []
            if current is None:
                active_kit = False
            elif active_kit and active_step is not None and _kit_clear_observed(
                    record, current, active_step, previous_pending, previous_attempt, seen_attempts):
                service_completed.extend(completed)
                active_kit = False
                reservations.pop(current['key'] + ':kit', None)
            if isinstance(ordinary_plan, dict) and 'steps' in ordinary_plan:
                from .skills import Plan
                plan = Plan.from_dict(ordinary_plan)
                snapshot = GameSnapshot(**record['after_state'])
                step = plan.steps[ordinary_index]
                verified = [value for value in completed if value.get('plan_id') == plan.id
                    and value.get('step_index') == ordinary_index and value.get('action') == step.action
                    and value.get('step_sha256') == fingerprint(asdict(step))
                    and _attempt_endpoint(value, asdict(step), record['state']) and step.satisfied(snapshot)]
                if len(verified) == 1:
                    service_completed.extend(verified)
                    ordinary_index += 1
                    reservations.pop(plan.id, None)
                if (ordinary_index == len(plan.steps) or record.get('action') == 'verify'
                        and record.get('verified') is True and previous_pending is None
                        and record.get('pending') is None and record.get('attempt') is None
                        and all(step.satisfied(snapshot) for step in plan.steps[ordinary_index:])):
                    reservations.pop(plan.id, None)
                    ordinary_plan = None
                elif (record.get('action') == 'observe' and record.get('verified') is False
                      and previous_pending is None and record.get('pending') is None
                      and record.get('attempt') is None and not step.allowed(snapshot)
                      and any(value.get('kind') == 'plan_failed' and value.get('plan') == plan.id
                              and value.get('tick') == now for value in fresh)):
                    reservations.pop(plan.id, None)
                    ordinary_plan = None
            for value in record.get('attempt_outcomes', []):
                if isinstance(value, dict) and not any(previous.get('id') == value.get('id') for previous in outcomes):
                    if (value.get('outcome') == 'verified' and value.get('action') in {'factory_extract', 'factory_insert'}
                            and not any(_same(value, candidate) for candidate in service_completed)):
                        issues.add('solid_funding_service_outcome_without_dispatch')
                        continue
                    outcomes.append(deepcopy(value))
            outcomes = outcomes[-64:]
            for value in [record.get('attempt'), *record.get('attempt_outcomes', [])]:
                if isinstance(value, dict) and isinstance(value.get('id'), str):
                    seen_attempts.add(value['id'])
            previous_tick = now
            previous_budgets = current_budgets
            previous_pending = record.get('pending')
            previous_attempt = record.get('attempt')
            previous_routes = record.get('after_state', {}).get('factory', {}).get('solid_routes', {}).get('routes', {})
            previous_history = history
        final_active = (working is not None and isinstance(final.get('active_plan'), dict)
                        and final['active_plan'].get('id') == working['key'] + ':kit')
        if final_active != active_kit:
            issues.add('solid_funding_final_active_plan_mismatch')
        if not final_active and not _same(ordinary_plan, final.get('active_plan')):
            issues.add('solid_funding_final_ordinary_plan_mismatch')
        final_events = _funding_events(final.get('history', []))
        if not _same(final.get('history', [])[-8:], rows[-1].get('history', [])):
            issues.add('solid_funding_final_history_record_mismatch')
        for event in final_events:
            _validate_transition(event, 0, final['last_tick'], intents)
            if hashlib.sha256(_encoded(event)).digest() not in seen:
                issues.add('solid_funding_final_history_unobserved')
        if not _initial_history_state(final_events, final.get('solid_funding'), final.get('failures', {})):
            issues.add('solid_funding_final_history_mismatch')
        if not _same(working, final.get('solid_funding')) or not _same(
                rows[-1].get('solid_funding'), final.get('solid_funding')):
            issues.add('solid_funding_final_checkpoint_mismatch')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, IndexError):
        issues.add('solid_funding_evidence_invalid')
    return sorted(issues)
