"""Offline, conservative continuity report for one immutable native campaign.

This verifies evidence boundaries. It cannot qualify native coal/solid routes or
authorize gameplay, deployment, or final acceptance.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path

from .acceptance_io import MAX_JSON, MAX_LOG, load_json, stable_read
from .campaign_progress import SCIENCE

SCHEMA = 'jev-factorio.cross-chunk-evidence.v1'
IDENTITY = ('session_id', 'save_sha256', 'source_commit', 'source_tree',
            'treatment_sha256', 'witness_sha256', 'attachment_receipt_sha256',
            'actor_unit', 'policy', 'requested_model', 'controller')
CHUNK = {'gameplay', 'start_byte', 'end_byte', 'span_sha256', 'before_checkpoint',
         'before_sha256', 'after_checkpoint', 'after_sha256', 'owner_result',
         'owner_result_sha256', 'owner_status'}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _oid(value: object) -> bool:
    return isinstance(value, str) and len(value) in (40, 64) and all(c in '0123456789abcdef' for c in value)


def _path(root: Path, name: object) -> Path:
    _require(isinstance(name, str) and name and '\\' not in name, 'Invalid evidence path')
    candidate = Path(name)
    _require(not candidate.is_absolute() and all(p not in ('', '.', '..') for p in candidate.parts),
             'Evidence path escapes manifest directory')
    return root / candidate


def _read(root: Path, name: object, maximum: int, expected: str) -> bytes:
    _require(_digest(expected), 'Invalid evidence digest')
    raw = stable_read(_path(root, name), maximum)
    _require(hashlib.sha256(raw).hexdigest() == expected, 'Evidence digest mismatch')
    return raw


def _stamp(value: object) -> datetime:
    _require(isinstance(value, str), 'Missing UTC timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid UTC timestamp') from exc
    _require(result.tzinfo is not None and result.utcoffset().total_seconds() == 0,
             'Expected UTC timestamp')
    return result.astimezone(timezone.utc)


def _counts(value: object) -> dict[str, int]:
    _require(isinstance(value, dict), 'Missing native science counter')
    _require(all(isinstance(k, str) and type(v) is int and v >= 0 for k, v in value.items()),
             'Invalid native science counter')
    return value


def _progress(state: dict) -> tuple[str, float, frozenset[str]]:
    factory = state['factory']
    name, amount = factory.get('research'), factory.get('research_progress')
    _require(isinstance(name, str) and type(amount) in (int, float) and 0 <= amount <= 1,
             'Missing research progress')
    completed = state.get('researched')
    _require(isinstance(completed, list) and all(isinstance(v, str) for v in completed),
             'Missing research completion list')
    return name, float(amount), frozenset(completed)


def _row_identity(row: dict, identity: dict) -> None:
    _require(row.get('session_id') == identity['session_id'] and row.get('world_kind') == 'fle'
             and row.get('controller') == identity['controller']
             and row.get('policy') == identity['policy']
             and row.get('requested_model') == identity['requested_model'],
             'Gameplay campaign identity drift')
    revision = row.get('code_revision')
    _require(isinstance(revision, dict) and revision.get('commit') == identity['source_commit']
             and revision.get('dirty') is False, 'Gameplay source drift')
    # Legacy rows do not carry every private witness digest. Their immutable
    # owner artifacts are bound by the manifest and still need native review.


def analyze(manifest_path: Path) -> dict:
    """Validate exact spans/chains and report bounded science measurement.

    Caller-provided owner dispositions and native routes are not trusted as
    proof of paid route flow; final native acceptance remains explicitly false.
    """
    manifest_path = Path(manifest_path)
    manifest_raw = stable_read(manifest_path, MAX_JSON)
    value = load_json(manifest_raw)
    _require(isinstance(value, dict) and set(value) == {'schema', 'identity', 'max_observation_gap_seconds',
             'max_science_stall_seconds', 'chunks'} and value['schema'] == SCHEMA,
             'Invalid cross-chunk manifest')
    identity = value['identity']
    _require(isinstance(identity, dict) and set(identity) == set(IDENTITY), 'Incomplete campaign identity')
    for key in IDENTITY:
        if key == 'actor_unit':
            valid = type(identity[key]) is int and identity[key] > 0
        elif key in ('source_commit', 'source_tree'):
            valid = _oid(identity[key])
        elif key.endswith('sha256'):
            valid = _digest(identity[key])
        else:
            valid = isinstance(identity[key], str) and bool(identity[key])
        _require(valid, 'Invalid campaign identity')
    for key in ('max_observation_gap_seconds', 'max_science_stall_seconds'):
        _require(type(value[key]) is int and 0 < value[key] <= 1800, 'Invalid predeclared gap bound')
    chunks = value['chunks']
    _require(isinstance(chunks, list) and 2 <= len(chunks) <= 10000, 'Expected bounded cross-chunk sequence')
    root = manifest_path.parent
    previous_checkpoint = previous_time = previous_tick = previous_counter = previous_research = None
    previous_path = previous_end = None
    first_progress = last_progress = None
    first_research = last_research = None
    lab_unit = None
    max_gap = max_stall = records = science_consumed = lab_delivered = model_calls = 0
    seen_rows: set[str] = set()
    seen_receipts: dict[str, dict] = {}
    seen_owner: set[str] = set()
    spans = []
    for chunk in chunks:
        _require(isinstance(chunk, dict) and set(chunk) == CHUNK, 'Invalid chunk evidence')
        _require(chunk['owner_status'] == 'verified' and chunk['owner_result_sha256'] not in seen_owner,
                 'Ambiguous or repeated owner outcome')
        seen_owner.add(chunk['owner_result_sha256'])
        _require(previous_checkpoint is None or previous_checkpoint == chunk['before_sha256'],
                 'Checkpoint chain gap')
        result = load_json(_read(root, chunk['owner_result'], MAX_JSON, chunk['owner_result_sha256']))
        _require(isinstance(result, dict) and result.get('status') == 'verified', 'Owner result not verified')
        before = load_json(_read(root, chunk['before_checkpoint'], MAX_JSON, chunk['before_sha256']))
        after = load_json(_read(root, chunk['after_checkpoint'], MAX_JSON, chunk['after_sha256']))
        for checkpoint in (before, after):
            _require(isinstance(checkpoint, dict) and checkpoint.get('session_id') == identity['session_id']
                     and checkpoint.get('status') == 'running'
                     and not any(checkpoint.get(k) for k in ('pending', 'attempt', 'background_job',
                                                             'background_attempt', 'reservations')),
                     'Checkpoint identity or idle ownership failed')
        previous_checkpoint = chunk['after_sha256']
        path = _path(root, chunk['gameplay'])
        raw = stable_read(path, MAX_LOG)
        start, end = chunk['start_byte'], chunk['end_byte']
        _require(type(start) is int and type(end) is int and 0 <= start < end <= len(raw)
                 and (start == 0 or raw[start - 1:start] == b'\n')
                 and raw[end - 1:end] == b'\n', 'Invalid gameplay byte span')
        _require(previous_path != chunk['gameplay'] or previous_end == start,
                 'Gap or overlap in reused gameplay log')
        previous_path, previous_end = chunk['gameplay'], end
        span = raw[start:end]
        digest = hashlib.sha256(span).hexdigest()
        _require(digest == chunk['span_sha256'], 'Gameplay span digest mismatch')
        spans.append({'sha256': digest, 'bytes': len(span), 'records': len(span.splitlines())})
        first_chunk_tick = last_chunk_tick = None
        for line in span.splitlines():
            _require(bool(line) and len(line) <= MAX_JSON, 'Invalid gameplay line')
            row = load_json(line)
            _require(isinstance(row, dict), 'Gameplay row must be object')
            row_hash = hashlib.sha256(line).hexdigest()
            _require(row_hash not in seen_rows, 'Repeated gameplay row')
            seen_rows.add(row_hash)
            _row_identity(row, identity)
            state = row.get('after_state')
            _require(isinstance(state, dict) and state.get('session_id') == identity['session_id'],
                     'Missing native after-state')
            tick = state.get('tick')
            _require(type(tick) is int and tick >= 0 and (previous_tick is None or tick > previous_tick),
                     'Native tick regression or duplicate')
            first_chunk_tick = first_chunk_tick if first_chunk_tick is not None else tick
            last_chunk_tick = tick
            now = _stamp(row.get('recorded_at_utc'))
            _require(previous_time is None or now > previous_time, 'Wall clock regression or duplicate')
            _require(row.get('status') not in ('blocked', 'uncertain', 'failed'), 'Stopped or ambiguous row')
            _require(type(row.get('model_call')) is bool, 'Missing JEV invocation evidence')
            model_calls += int(row['model_call'])
            factory = state.get('factory')
            _require(isinstance(factory, dict), 'Missing native factory observation')
            runtime = factory.get('acceptance_runtime')
            _require(isinstance(runtime, dict) and runtime.get('actor_unit') == identity['actor_unit']
                     and runtime.get('session_id') == identity['session_id']
                     and runtime.get('speed') == 1 and runtime.get('tick_paused') is False,
                     'Native actor/runtime identity drift')
            consumed = _counts(factory.get('consumed'))
            research = _progress(state)
            lab = factory.get('entities', {}).get('utility:lab')
            _require(isinstance(lab, dict) and lab.get('name') == 'lab'
                     and type(lab.get('unit_number')) is int and lab['unit_number'] > 0
                     and factory.get('force_entity_counts', {}).get('lab') == 1,
                     'Exclusive owned lab not observed')
            _require(lab_unit is None or lab_unit == lab['unit_number'], 'Owned lab identity drift')
            lab_unit = lab['unit_number']
            first_research = first_research or research
            last_research = research
            if previous_time is not None:
                gap = (now - previous_time).total_seconds()
                max_gap = max(max_gap, gap)
                _require(gap <= value['max_observation_gap_seconds'], 'Observation gap')
                _require(all(consumed.get(k, 0) >= v for k, v in previous_counter.items()),
                         'Science counter regression')
                _require(previous_research[2] <= research[2], 'Research completion regression')
                delta = sum(consumed.get(k, 0) - previous_counter.get(k, 0) for k in SCIENCE)
                science_consumed += delta
                advanced = bool(research[2] - previous_research[2]) or (
                    research[0] == previous_research[0] and research[1] > previous_research[1])
                if delta > 0 and advanced:
                    if last_progress is not None:
                        stall = (now - last_progress).total_seconds()
                        max_stall = max(max_stall, stall)
                        _require(stall <= value['max_science_stall_seconds'], 'Science progress stall')
                    first_progress = first_progress or now
                    last_progress = now
            # Lab delivery is a receipt-level claim; no force-production proxy.
            receipts = factory.get('receipts')
            _require(isinstance(receipts, dict), 'Missing native receipt ledger')
            for receipt_id, receipt in receipts.items():
                _require(isinstance(receipt_id, str) and isinstance(receipt, dict), 'Malformed native receipt')
                _require(receipt_id not in seen_receipts or seen_receipts[receipt_id] == receipt,
                         'Native receipt identity rewritten')
                if receipt_id not in seen_receipts and records > 0 and receipt.get('role') == 'utility:lab' \
                        and receipt.get('item') in SCIENCE and receipt.get('extracting') is False:
                    quantity = receipt.get('quantity')
                    _require(type(quantity) is int and quantity > 0 and type(receipt.get('tick')) is int
                             and previous_tick < receipt['tick'] <= tick
                             and receipt.get('unit_number') == lab_unit, 'Unbound lab delivery receipt')
                    lab_delivered += quantity
                seen_receipts[receipt_id] = receipt
            previous_time, previous_tick, previous_counter, previous_research = now, tick, consumed, research
            records += 1
        _require(type(before.get('last_tick')) is int and before['last_tick'] <= first_chunk_tick
                 and after.get('last_tick') == last_chunk_tick, 'Checkpoint/gameplay tick boundary mismatch')
    useful = (last_progress - first_progress).total_seconds() if first_progress and last_progress else 0
    issues = ['native_paid_coal_and_downstream_rollup_required', 'owner_receipt_body_qualification_required']
    if useful < 1800:
        issues.append('continuous_useful_1800_seconds_missing')
    if lab_delivered <= 0:
        issues.append('new_paid_lab_delivery_missing')
    if science_consumed <= 0 or (first_research[0] == last_research[0]
                                 and last_research[1] <= first_research[1]
                                 and last_research[2] == first_research[2]):
        issues.append('science_consumption_or_research_progress_missing')
    if model_calls == 0:
        issues.append('actual_jev_model_call_missing')
    return {'schema': SCHEMA, 'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
            'identity': identity, 'spans': spans, 'records': records,
            'final_checkpoint_sha256': previous_checkpoint,
            'useful_seconds_lower_bound': useful, 'science_consumed_delta': science_consumed,
            'lab_delivered_by_new_receipts': lab_delivered,
            'jev_model_calls': model_calls,
            'research_completions': sorted(last_research[2] - first_research[2]),
            'max_observation_gap_seconds': max_gap, 'max_science_stall_seconds': max_stall,
            'issues': issues, 'native_acceptance_proven': False,
            'deployment_authorized': False}
