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
                  allow_before: bool = False) -> bool:
    for label in (('state', 'after_state') if allow_before else ('after_state',)):
        factory = record.get(label, {}).get('factory', {})
        row = factory.get('solid_routes', {}).get('routes', {}).get(state['route'])
        if not isinstance(row, dict) or not solid_funding.bound(state, row):
            continue
        # The parent analyzer separately validates the full paid prefix, native
        # receipts and exact initial/final controller commitments.  We require
        # that proof here, not merely a proposed route with the same string ID.
        if paid:
            if row.get('state') in {'building', 'ready'} and row.get('parts'):
                return True
        elif row.get('state') == 'proposed':
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
        for record in rows:
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
            new_commits = 0
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
                    if (starting and (proof['actions'] != 1 or proof['started_tick'] != event_tick)
                            or not starting and (not _same(_identity(working), _identity(proof))
                                                 or proof['actions'] != working['actions'] + 1)
                            or prior_budget is None or prior_budget >= 2
                            or proof['key'] in abandoned_keys
                            or not _native_bound(proof, record, allow_before=True)):
                        issues.add('solid_funding_commit_not_reconciled')
                    else:
                        working = deepcopy(proof)
                else:
                    if working is None or not _same(working, proof):
                        issues.add('solid_funding_release_proof_mismatch')
                        continue
                    if kind == 'solid_kit_abandoned':
                        count = _budget(current_budgets, proof['key'] + ':kit')
                        reason = event.get('reason')
                        if count is None or count < 2 or not isinstance(reason, str) or not 0 < len(reason) <= 128:
                            issues.add('solid_funding_abandonment_not_reconciled')
                            continue
                        # Later events cannot reuse the pre-record budget after
                        # this transition established that the project is spent.
                        abandoned_keys.add(proof['key'])
                    elif not _native_bound(proof, record, paid=True):
                        issues.add('solid_funding_paid_handoff_not_observed')
                        continue
                    working = None
            if not _same(working, current):
                issues.add('solid_funding_record_history_mismatch')
            # A receipt may clear pending only after the observer's funding
            # reconciliation was deferred. Retaining the lock across that
            # boundary is safe; payment alone must not silently release it.
            if current is not None and not (_native_bound(current, record)
                                            or _native_bound(current, record, paid=True)):
                issues.add('solid_funding_owned_proposal_not_observed')
            if not policy and (current is not None or working is not None
                               or _funding_events(record.get('history', []))):
                issues.add('solid_funding_disabled_policy_has_state')
            previous_tick = now
            previous_budgets = current_budgets
        if not _same(working, final.get('solid_funding')) or not _same(
                rows[-1].get('solid_funding'), final.get('solid_funding')):
            issues.add('solid_funding_final_checkpoint_mismatch')
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        issues.add('solid_funding_evidence_invalid')
    return sorted(issues)
