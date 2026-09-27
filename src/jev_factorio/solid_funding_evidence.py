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

from .planning import solid_funding
from .telemetry import validate_attempt

SCHEMA = 1
KINDS = frozenset({'solid_kit_committed', 'solid_kit_abandoned', 'solid_kit_paid_handoff'})


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
    return current[overlap:]


def _kit_plan_observed(proof: dict, record: dict, tick: int, fresh: list) -> bool:
    action = record.get('action')
    compatible = action in {'factory_craft', 'factory_extract', 'observe'} or (
        action == 'verify' and record.get('verified') is True)
    plans = [(index, event) for index, event in enumerate(fresh)
             if event.get('kind') == 'plan_committed']
    return compatible and len(plans) == 1 and any(
        set(event) == {'kind', 'plan', 'source', 'tick'}
        and event.get('kind') == 'plan_committed' and event.get('plan') == proof['key'] + ':kit'
        and event.get('source') in {'deterministic', 'deterministic-singleton',
                                     'deterministic-fallback', 'jev', 'mock'}
        and _tick(event.get('tick')) and event['tick'] == tick
        and any(value.get('kind') == 'solid_kit_committed' and _same(value.get('funding'), proof)
                and value.get('tick') == tick for value in fresh[:index])
        for index, event in plans)


def _abandonment_observed(proof: dict, event: dict, record: dict,
                         prior_budget: int | None, fresh: list) -> bool:
    reason, tick = event['reason'], event['tick']
    fresh = fresh[:next((index for index, value in enumerate(fresh) if _same(value, event)), 0)]
    if reason == 'kit_deadline':
        return tick >= proof['deadline_tick']
    if reason == 'kit_failure_budget':
        return prior_budget is not None and prior_budget >= 2 or any(
            set(value) == {'kind', 'plan', 'reason', 'tick'}
            and value.get('kind') == 'plan_failed' and value.get('plan') == proof['key'] + ':kit'
            and isinstance(value.get('reason'), str) and bool(value['reason'])
            and _tick(value.get('tick')) and value['tick'] == tick for value in fresh)
    observations = [name for name in ('state', 'after_state')
                    if record.get(name, {}).get('tick') == tick]
    if reason == 'kit_endpoint_or_layout_changed':
        return any(not (_native_bound(proof, record, observation=name)
                        or _native_bound(proof, record, paid=True, observation=name))
                   for name in observations)
    if reason == 'kit_action_budget' and proof['actions'] == solid_funding.MAX_ACTIONS:
        from .solid_routes import remaining
        return any(any(record[name].get('inventory', {}).get(item, 0) < count
                       for item, count in remaining(record[name]['factory']['solid_routes']['routes'][proof['route']]).items())
                   for name in observations if _native_bound(proof, record, observation=name))
    # Catalog/payback recomputation and intermediate observations are not in
    # retained records. An exhausted count written by that observer is not
    # independent evidence of the trigger. Mark that window unmeasurable.
    return False


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
            return True
    return False


def _dispatch_crossed_deadline(record: dict, before: int, deadline: int, now: int) -> bool:
    # Any ordinary controller action may run while optional funding is held;
    # infrastructure does not replace the science/maintenance frontier. Mere
    # action labels, or old outcomes repeated in the ring, do not prove dispatch.
    action = record.get('action')
    if not isinstance(action, str) or action in {'observe', 'verify'}:
        return False
    def started(value):
        return (isinstance(value, dict) and value.get('action') == action
                and _tick(value.get('started_tick'))
                and before <= value['started_tick'] < deadline)
    pending = record.get('pending')
    if started(pending) and pending.get('dispatch') in {'returned', 'ambiguous'}:
        attempt = record.get('attempt')
        if started(attempt) and attempt['started_tick'] == pending['started_tick']:
            validate_attempt(attempt)
            return True
    outcomes = record.get('attempt_outcomes', [])
    if record.get('verified') is True and isinstance(outcomes, list):
        for value in outcomes:
            if (started(value) and value.get('outcome') == 'verified'
                    and _tick(value.get('finished_tick')) and value['finished_tick'] == now):
                validate_attempt(value, finished=True)
                return True
    return False


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
        if working is not None:
            solid_funding.validate_state(working, previous_tick, intents)
        # The ring repeats old events. Seed it from the initial checkpoint and
        # process new event values only once. Hash storage is bounded by input
        # record count (already bounded by the parent analyzer's byte limits).
        seen = {hashlib.sha256(_encoded(e)).digest()
                for e in _funding_events(initial.get('history', []))}
        previous_budgets = initial.get('failures', {})
        previous_pending = initial.get('pending')
        previous_attempt = initial.get('attempt')
        previous_routes = initial.get('solid_commitments', {})
        previous_history = initial.get('history', [])
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
            if any(value.get('kind') == 'plan_committed' and (
                    not _tick(value.get('tick')) or value['tick'] > now) for value in history):
                issues.add('solid_funding_plan_history_invalid')
            prior = deepcopy(working)
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
                fingerprint = hashlib.sha256(_encoded(event)).digest()
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                kind = event['kind']
                proof = event.get('funding')
                event_tick = event.get('tick')
                expected_fields = {'kind', 'key', 'tick', 'funding'}
                if kind == 'solid_kit_abandoned':
                    expected_fields.add('reason')
                if (kind not in KINDS or set(event) != expected_fields
                        or not _tick(event_tick) or not previous_tick <= event_tick <= now):
                    issues.add('solid_funding_transition_invalid')
                    continue
                try:
                    solid_funding.validate_state(proof, event_tick, intents)
                except (ValueError, TypeError, KeyError, AttributeError):
                    issues.add('solid_funding_transition_proof_invalid')
                    continue
                expected_key = proof['key'] + ('' if kind == 'solid_kit_paid_handoff' else ':kit')
                if event.get('key') != expected_key:
                    issues.add('solid_funding_transition_key_mismatch')
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
                            or previous_pending is not None
                            or proof['key'] in abandoned_keys
                            or not _tick(before_tick) or event_tick != before_tick
                            or event_tick >= proof['deadline_tick']
                            or not _kit_plan_observed(proof, record, before_tick, fresh)
                            or not _native_bound(proof, record, observation='state')):
                        issues.add('solid_funding_commit_not_reconciled')
                    else:
                        working = deepcopy(proof)
                        committed_proof = proof
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
                        if not _abandonment_observed(proof, event, record,
                                _budget(previous_budgets, proof['key'] + ':kit'), fresh):
                            issues.add('solid_funding_abandonment_trigger_unproven')
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
            payment_proof = prior or working or current or committed_proof
            if payment_proof is not None:
                route = payment_proof['route']
                before_routes = record.get('state', {}).get('factory', {}).get('solid_routes', {}).get('routes', {})
                after_routes = record.get('after_state', {}).get('factory', {}).get('solid_routes', {}).get('routes', {})
                carried = [previous_attempt] if (isinstance(previous_pending, dict)
                            and previous_pending.get('action') == 'factory_solid_build') else []
                if not _paid_growth_observed(previous_routes.get(route, {}), before_routes.get(route, {}),
                                             payment_proof, carried, before_tick):
                    issues.add('solid_funding_paid_prefix_without_dispatch')
                dispatched = list(carried)
                if record.get('action') == 'factory_solid_build':
                    if isinstance(record.get('pending'), dict):
                        dispatched.append(record.get('attempt'))
                    dispatched.extend(value for value in record.get('attempt_outcomes', [])
                        if isinstance(value, dict) and value.get('outcome') == 'verified'
                        and _tick(value.get('started_tick')) and value['started_tick'] >= before_tick
                        and value.get('finished_tick') == now and record.get('verified') is True)
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
                if budget is None or budget >= 2:
                    issues.add('solid_funding_retained_budget_exhausted')
                if (previous_pending is None and _tick(before_tick)
                        and (before_tick >= current['deadline_tick']
                             or now >= current['deadline_tick'] and not _dispatch_crossed_deadline(
                                 record, before_tick, current['deadline_tick'], now))):
                    issues.add('solid_funding_expired_state_retained')
            if not policy and (current is not None or working is not None
                               or _funding_events(record.get('history', []))):
                issues.add('solid_funding_disabled_policy_has_state')
            previous_tick = now
            previous_budgets = current_budgets
            previous_pending = record.get('pending')
            previous_attempt = record.get('attempt')
            previous_routes = record.get('after_state', {}).get('factory', {}).get('solid_routes', {}).get('routes', {})
            previous_history = history
        if not _same(working, final.get('solid_funding')) or not _same(
                rows[-1].get('solid_funding'), final.get('solid_funding')):
            issues.add('solid_funding_final_checkpoint_mismatch')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        issues.add('solid_funding_evidence_invalid')
    return sorted(issues)
