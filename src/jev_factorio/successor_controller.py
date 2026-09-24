"""Opt-in additive producer lifecycle over the existing single dispatcher."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json

from . import successors as contract
from .planning.successors import SuccessorPlanner, marked
from .planning.decision_support import candidate_evidence
from .production_sites import sources as site_sources


def _pending_step(loop):
    plan = loop.memory.active_plan or {}
    steps = plan.get('steps', [])
    return steps[loop.memory.step_index] if loop.memory.pending and loop.memory.step_index < len(steps) else {}


def _extend_paid(before, after, step, source, action, layout):
    if any(after.get(part) != receipt for part, receipt in before.items()):
        raise ValueError('Successor paid identity disappeared or changed')
    new = set(after) - set(before)
    p = step.get('parameters') or {}
    if new and (len(new) != 1 or step.get('action') != action or p.get('source') != source
                or p.get('layout') != layout or p.get('part') not in new
                or after[p['part']]['receipt'] != p.get('receipt')):
        raise ValueError('Untracked successor construction')


def _empty_receipts():
    return {'output_layout': None, 'input_layout': None, 'output': {}, 'input': {}, 'use': {}, 'qualification': {}}


class SuccessorMixin:
    planner_type = SuccessorPlanner

    def __init__(self, backend, jev=None, **options):
        if options.get('target') != 'rocket_launch' or options.get('factory_scheduling') != 'ready-work':
            raise ValueError('Successors require ready-work rocket planning')
        self._successors_enabled = True
        self._successor_fault = False
        self._successor_evidence = {}
        super().__init__(backend, jev, **options)
        native = getattr(backend, '_factory', None)
        if native is not None:
            from .backends import has_adapter
            from .backends.successors import SuccessorFactory
            if not has_adapter(native, SuccessorFactory):
                backend._factory = SuccessorFactory(native)
        elif getattr(backend, 'successors_supported', False) is not True:
            raise ValueError('Backend does not support successor evidence')

    def _observe(self, stage='observe'):
        snapshot = super()._observe(stage)
        try:
            rows = contract.sources(snapshot)
            step = _pending_step(self)
            for source, project in self.memory.successor_projects.items():
                contract.project_valid(project, source, snapshot.tick)
                old = snapshot.factory.get('entities', {}).get('recipe:' + source[7:], {})
                if old.get('unit_number') != project['predecessor_unit']:
                    raise ValueError('Successor predecessor was replaced')
                if project['source_unit'] and source not in rows:
                    raise ValueError('Owned successor disappeared')
            for source, row in rows.items():
                project = self.memory.successor_projects.get(source)
                if not project or project['anchor'] != row['anchor'] or project['predecessor_unit'] != row['predecessor_unit']:
                    raise ValueError('Untracked successor intent')
                if project['source_unit'] != row['source_unit']:
                    p = step.get('parameters') or {}
                    if (project['source_unit'] or not row['source_unit'] or step.get('action') != 'factory_place'
                            or p.get('role') != source or p.get('anchor') != row['anchor']):
                        raise ValueError('Untracked successor furnace')
                    project['source_unit'] = row['source_unit']
                    self.memory.event('successor_source_bound', source=source, source_unit=row['source_unit'], tick=snapshot.tick)
                before = self.memory.successor_receipts.get(source, _empty_receipts())
                output = snapshot.factory.get('output_buffers', {}).get('sources', {}).get(source, {})
                route = snapshot.factory.get('input_routes', {}).get('sources', {}).get(source, {})
                for label, row_native, action in [('output', output, 'factory_buffer_build'), ('input', route, 'factory_input_build')]:
                    parts = row_native.get('parts', {})
                    layout = row_native.get('layout')
                    if before[label + '_layout'] and before[label + '_layout'] != layout:
                        raise ValueError('Successor route layout changed')
                    _extend_paid(before[label], parts, step, source, action, layout)
                for proof in ('use', 'qualification'):
                    if before[proof] and before[proof] != row[proof]:
                        raise ValueError('Successor proof regressed')
                self.memory.successor_receipts[source] = {
                    'output_layout': output.get('layout') if output.get('parts') else before['output_layout'],
                    'input_layout': route.get('layout') if route.get('parts') else before['input_layout'],
                    'output': deepcopy(output.get('parts', {})), 'input': deepcopy(route.get('parts', {})),
                    'use': deepcopy(row['use']), 'qualification': deepcopy(row['qualification'])}
                if contract.qualified(source, snapshot) and project['status'] != 'qualified':
                    project['status'] = 'qualified'
                    self.memory.event('successor_qualified', source=source, proof=deepcopy(row['qualification']), tick=snapshot.tick)
        except (ValueError, KeyError, TypeError, AttributeError):
            self._successor_fault = True
            self.memory.status = 'uncertain'
            self.memory.reason = 'Successor evidence invalid; preserve predecessor, paid assets and pending work'
        self._successor_evidence = deepcopy(snapshot.factory.get('successors', {}))
        self._save()
        return snapshot

    def _execution_barrier(self, snapshot):
        return self._successor_fault or super()._execution_barrier(snapshot)

    def _record_extras(self):
        return {**super()._record_extras(), 'ore_side_successors': True,
                'successor_evidence': deepcopy(self._successor_evidence),
                'successor_projects': deepcopy(self.memory.successor_projects)}

    def _commit_successor(self, plan, snapshot):
        marker = (plan.materials or {}).get(contract.MARKER)
        if not marker:
            return
        if (not isinstance(marker, dict) or set(marker) != {'source', 'anchor', 'predecessor_unit', 'observed_tick'}
                or marker['source'] not in contract.ROLES or marker['observed_tick'] != snapshot.tick):
            raise ValueError('Invalid successor commitment marker')
        source = marker['source']
        site = site_sources(snapshot).get(source, {})
        if marker['anchor'] != site.get('anchor'):
            raise ValueError('Stale successor proposal')
        project = self.memory.successor_projects.get(source)
        if project is None:
            if (self.memory.capital_investment or self.memory.pending or getattr(self.memory, 'background_job', None)
                    or any(p['status'] != 'qualified' for p in self.memory.successor_projects.values())
                    or self.memory.failures.get('successor:' + source, 0) >= 2):
                raise ValueError('Successor project conflict or exhausted budget')
            project = {'anchor': marker['anchor'], 'predecessor_unit': marker['predecessor_unit'],
                       'source_unit': 0, 'started_tick': snapshot.tick,
                       'deadline_tick': snapshot.tick + contract.MAX_PROJECT_TICKS, 'status': 'active'}
            self.memory.successor_projects[source] = project
            self.memory.event('successor_committed', source=source, project=deepcopy(project), tick=snapshot.tick)
        if project['status'] != 'active' or project['anchor'] != marker['anchor']:
            raise ValueError('Successor project is paused or differs')

    def _pause_successor(self, source, reason):
        project = self.memory.successor_projects[source]
        if project['status'] == 'active':
            project['status'] = 'paused'
            key = 'successor:' + source
            self.memory.failures[key] = max(2, self.memory.failures.get(key, 0))
            self.memory.event('successor_paused', source=source, reason=reason, tick=self.memory.last_tick)
            self._save()

    def _fail_plan(self, reason):
        plan = self.memory.active_plan or {}
        marker = (plan.get('materials') or {}).get(contract.MARKER)
        plan_id = plan.get('id')
        super()._fail_plan(reason)
        if marker and self.memory.failures.get(plan_id, 0) >= 2:
            self._pause_successor(marker['source'], 'reconciled_step_budget')

    def _work_candidates(self, snapshot):
        # Do not open a competing capital project while a successor owns a kit.
        if any(p['status'] == 'active' for p in self.memory.successor_projects.values()):
            return self._compile_candidates(snapshot)
        return super()._work_candidates(snapshot)

    def _compile_candidates(self, snapshot):
        original, blocker = super()._compile_candidates(snapshot)
        if (self.memory.active_goal != 'rocket_launch' or self.memory.capital_investment
                or getattr(self.memory, 'background_job', None) or snapshot.factory.get('crafting_queue', 0)):
            return original, blocker
        evidence = candidate_evidence(snapshot, self.catalog, original)
        urgent = [p for p in original if evidence[p.id]['urgency'] >= 2]
        if urgent:
            return urgent, blocker
        sites = site_sources(snapshot)
        planner = self.planner_type(self.catalog, snapshot, 'rocket_launch')
        for source, project in self.memory.successor_projects.items():
            if project['status'] != 'active':
                continue
            if snapshot.tick >= project['deadline_tick']:
                self._pause_successor(source, 'bounded_project_deadline')
                return original, blocker
            try:
                plan = planner.continuation(source, project['anchor'])
                if plan:
                    plan = marked(plan, source, sites[source], snapshot)
                    if self.memory.failures.get(plan.id, 0) >= 2:
                        self._pause_successor(source, 'retained_plan_failure_budget')
                    elif self._step_allowed(plan.steps[0], snapshot):
                        tracked = getattr(self, '_tracked_plan', None)
                        return [tracked(plan, snapshot) if tracked else plan], ''
            except (ValueError, KeyError):
                self._pause_successor(source, 'no_safe_continuation')
            return original, blocker
        # Do not construct a second successor behind a paused partial project.
        if any(p['status'] != 'qualified' for p in self.memory.successor_projects.values()):
            return original, blocker
        for source, ore in sorted(contract.ROLES.items()):
            if source in self.memory.successor_projects or self.memory.failures.get('successor:' + source, 0) >= 2:
                continue
            site = sites.get(source, {})
            old_role = 'recipe:' + source[7:]
            old = snapshot.factory.get('entities', {}).get(old_role, {})
            if (site.get('state') != 'proposed' or old.get('products_finished', 0) < 20
                    or old_role in snapshot.factory.get('input_routes', {}).get('sources', {})):
                continue
            manual = any((p.steps[0].action == 'factory_gather' and p.steps[0].parameters.get('resource') == ore
                          or p.steps[0].action == 'factory_insert' and p.steps[0].parameters.get('role') == old_role
                          and p.steps[0].parameters.get('item') == ore)
                         and p.steps[0].parameters.get('quantity', 0) >= 10 for p in original)
            if not manual:
                continue
            try:
                plan = planner.continuation(source, site['anchor'])
                if plan and self._step_allowed(plan.steps[0], snapshot):
                    plan = marked(plan, source, site, snapshot)
                    tracked = getattr(self, '_tracked_plan', None)
                    return [tracked(plan, snapshot) if tracked else plan], ''
            except (ValueError, KeyError):
                continue
        return original, blocker


def successor_loop_type(base):
    if (not hasattr(base.memory_type, 'input_routes_schema') or not hasattr(base.memory_type, 'background_schema')
            or hasattr(base.memory_type, 'outposts_schema')):
        raise ValueError('Successors require background-work input routes without mining outposts')

    @dataclass
    class SuccessorMemory(base.memory_type):
        successor_schema: int = 1
        successor_projects: dict = field(default_factory=dict)
        successor_receipts: dict = field(default_factory=dict)

        @classmethod
        def load(cls, path, session_id, target):
            data = json.loads(path.read_text(encoding='utf-8'))
            keys = {'successor_schema', 'successor_projects', 'successor_receipts'}
            legacy = not keys.intersection(data)
            if not legacy and not keys <= data.keys():
                raise ValueError('Incomplete successor checkpoint extension')
            memory = super().load(path, session_id, target)
            if legacy:
                if (memory.status != 'running' or memory.active_plan or memory.pending or memory.reservations
                        or memory.capital_investment or getattr(memory, 'background_job', None)):
                    raise ValueError('Enable successors only at an idle, reconciled boundary')
                memory.event('successors_enabled', tick=memory.last_tick, reason='explicit_idle_boundary_capability')
            if (not contract.integer(memory.successor_schema, 1, 1)
                    or not isinstance(memory.successor_projects, dict)
                    or not isinstance(memory.successor_receipts, dict)
                    or set(memory.successor_projects) - contract.ROLES.keys()
                    or set(memory.successor_receipts) - memory.successor_projects.keys()
                    or sum(p.get('status') != 'qualified' for p in memory.successor_projects.values()) > 1):
                raise ValueError('Invalid successor checkpoint')
            for source, project in memory.successor_projects.items():
                contract.project_valid(project, source, max(0, memory.last_tick))
            for source, receipt in memory.successor_receipts.items():
                if not isinstance(receipt, dict) or set(receipt) != set(_empty_receipts()):
                    raise ValueError('Invalid successor receipt checkpoint')
                units, roles, ids = {memory.successor_projects[source]['source_unit']}, set(), set()
                for label, maximum in [('input', 66), ('output', 2)]:
                    parts, layout = receipt[label], receipt[label + '_layout']
                    if (not isinstance(parts, dict) or len(parts) > maximum
                            or layout is not None and not contract.text(layout) or parts and not layout):
                        raise ValueError('Invalid retained successor components')
                    for part, paid in parts.items():
                        if (not isinstance(paid, dict) or set(paid) != {'role', 'receipt', 'unit_number', 'paid'}
                                or not contract.text(part) or not contract.text(paid['role'])
                                or not contract.text(paid['receipt']) or not contract.integer(paid['unit_number'], 1)
                                or not contract.integer(paid['paid'], 1, 1) or paid['unit_number'] in units
                                or paid['role'] in roles or paid['receipt'] in ids):
                            raise ValueError('Invalid successor paid identity')
                        units.add(paid['unit_number']); roles.add(paid['role']); ids.add(paid['receipt'])
                if any(not isinstance(receipt[k], dict) for k in ('use', 'qualification')):
                    raise ValueError('Invalid retained successor proof')
            return memory

    return type('SuccessorLoop', (SuccessorMixin, base), {'memory_type': SuccessorMemory, '__module__': __name__})
