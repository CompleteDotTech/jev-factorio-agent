"""Offline acceptance-boundary checks; never repair a checkpoint or a game."""
from __future__ import annotations

from importlib.resources import files
from types import SimpleNamespace

from .acceptance_io import canonical, sha256
from . import successors


def probe_source_sha256() -> str:
    """Use the same UTF-8 source representation hashed by dev_preflight.probe.

    This checks the reported query identity, not that unsigned evidence truly
    came from executing that query. External authenticity remains unproven.
    """
    source = files('jev_factorio').joinpath('lua/acceptance_probe.lua').read_text(encoding='utf-8')
    return sha256(source.encode('utf-8'))


def _same(left: object, right: object) -> bool:
    # JSON identity is type-sensitive: True must not equal a paid count of 1.
    return canonical(left) == canonical(right)


def final_successor_issues(initial: dict, final: dict, record: dict,
                           observed_sources: set[str]) -> list[str]:
    """Compare immutable evidence, without calling a controller or adopting assets.

    An unplaced acquisition intent can exist without a native source. A source
    seen during the trial, a paid furnace, or retained parts cannot disappear at
    its final boundary. Existing checkpoint loaders remain schema authority.
    """
    snapshot = record['after_state']
    factory = snapshot.get('factory', {})
    initial_projects = initial.get('successor_projects', {})
    projects = final.get('successor_projects', {})
    receipts = final.get('successor_receipts', {})
    native = factory.get('successors', {})
    raw_sources = native.get('sources', {}) if isinstance(native, dict) else None
    if not (observed_sources or initial_projects or projects or receipts or raw_sources):
        return []
    issues: set[str] = set()
    if (type(final.get('successor_schema')) is not int or final['successor_schema'] != 1
            or not isinstance(projects, dict) or not isinstance(receipts, dict)):
        return ['final_successor_checkpoint_missing']
    if not _same(record.get('successor_projects'), projects):
        issues.add('final_successor_project_log_mismatch')
    if not isinstance(raw_sources, dict):
        return sorted(issues | {'final_successor_observation_invalid'})
    view = SimpleNamespace(session_id=final['session_id'], tick=snapshot['tick'], factory=factory)
    try:
        rows = successors.sources(view)
    except (ValueError, TypeError, KeyError, AttributeError):
        return sorted(issues | {'final_successor_observation_invalid'})
    if not observed_sources <= rows.keys() or not rows.keys() <= projects.keys():
        issues.add('final_successor_source_missing')
    if rows and not _same(record.get('successor_evidence'), native):
        issues.add('final_successor_evidence_log_mismatch')
    if not initial_projects.keys() <= projects.keys():
        issues.add('retained_successor_project_missing')
    entities = factory.get('entities', {})
    for source, project in projects.items():
        try:
            successors.project_valid(project, source, snapshot['tick'])
            old = initial_projects.get(source)
            if old:
                fixed = ('anchor', 'predecessor_unit', 'started_tick', 'deadline_tick')
                if (any(not _same(project[k], old[k]) for k in fixed)
                        or old['source_unit'] and not _same(project['source_unit'], old['source_unit'])
                        or old['status'] == 'qualified' and project['status'] != 'qualified'):
                    issues.add('retained_successor_identity_regressed')
            predecessor = entities.get('recipe:' + source[7:], {})
            if (predecessor.get('name') != 'stone-furnace'
                    or not _same(predecessor.get('unit_number'), project['predecessor_unit'])):
                issues.add('final_successor_predecessor_mismatch')
            row = rows.get(source)
            if row is None:
                if project['source_unit'] or source in observed_sources or receipts.get(source):
                    issues.add('final_successor_source_missing')
                # Acquiring a kit before placing the furnace is valid unfinished work.
                continue
            if (any(not _same(project[k], row[k]) for k in ('anchor', 'predecessor_unit', 'source_unit'))
                    or not project['started_tick'] <= row['started_tick'] <= snapshot['tick']):
                issues.add('final_successor_project_identity_mismatch')
            preferred = row['phase'] == 'preferred'
            if (preferred != (project['status'] == 'qualified')
                    or preferred and not successors.qualified(source, view)):
                issues.add('final_successor_qualification_mismatch')
            retained = receipts.get(source)
            if not isinstance(retained, dict):
                issues.add('final_successor_receipts_missing')
                continue
            for label, family in (('output', 'output_buffers'), ('input', 'input_routes')):
                native_route = factory.get(family, {}).get('sources', {}).get(source, {})
                parts = native_route.get('parts', {})
                if (not isinstance(parts, dict) or not _same(retained.get(label), parts)
                        or not _same(retained.get(label + '_layout'), native_route.get('layout') if parts else None)):
                    issues.add('final_successor_' + label + '_receipts_mismatch')
                if parts:
                    if not _same(native_route.get('source_unit'), project['source_unit']):
                        issues.add('final_successor_route_source_mismatch')
                    for paid in parts.values():
                        entity = entities.get(paid['role'], {})
                        if not _same(entity.get('unit_number'), paid['unit_number']):
                            issues.add('final_successor_paid_entity_missing')
            for proof in ('use', 'qualification'):
                if not _same(retained.get(proof), row[proof]):
                    issues.add('final_successor_' + proof + '_mismatch')
            old_receipts = initial.get('successor_receipts', {}).get(source, {})
            for key, value in old_receipts.items():
                if key in {'input', 'output'}:
                    if any(not _same(retained.get(key, {}).get(part), paid) for part, paid in value.items()):
                        issues.add('retained_successor_receipt_regressed')
                elif value and not _same(retained.get(key), value):
                    issues.add('retained_successor_proof_or_layout_regressed')
        except (ValueError, TypeError, KeyError, AttributeError):
            issues.add('final_successor_boundary_invalid')
    if not receipts.keys() <= projects.keys():
        issues.add('final_successor_untracked_receipts')
    for family in ('output_buffers', 'input_routes'):
        for source in factory.get(family, {}).get('sources', {}):
            if source in successors.ROLES and source not in rows:
                issues.add('final_successor_route_without_source')
    return sorted(issues)


def successor_history_issues(records: list[dict]) -> list[str]:
    """A matching final pair cannot excuse lost or reassigned prior proof."""
    seen: dict[str, dict] = {}
    issues: set[str] = set()
    for record in records:
        for label in ('state', 'after_state'):
            native = record[label].get('factory', {}).get('successors', {})
            rows = native.get('sources', {}) if isinstance(native, dict) else {}
            if not isinstance(rows, dict):
                issues.add('successor_history_invalid')
                continue
            if any(old.get('source_unit') and role not in rows for role, old in seen.items()):
                issues.add('successor_paid_evidence_disappeared')
            for role, row in rows.items():
                if not isinstance(row, dict):
                    issues.add('successor_history_invalid')
                    continue
                old = seen.get(role)
                if old:
                    if any(not _same(old.get(key), row.get(key)) for key in
                           ('anchor', 'predecessor_unit', 'started_tick')):
                        issues.add('successor_identity_history_changed')
                    if old.get('source_unit') and not _same(old['source_unit'], row.get('source_unit')):
                        issues.add('successor_identity_history_changed')
                    for proof in ('use', 'qualification'):
                        if old.get(proof) and not _same(old[proof], row.get(proof)):
                            issues.add('successor_' + proof + '_history_regressed')
                seen[role] = row  # Read-only references to immutable input records.
    return sorted(issues)
