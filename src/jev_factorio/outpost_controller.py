"""Opt-in outpost ownership, layered over the existing single native dispatcher."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from types import SimpleNamespace

from .mining_outposts import COMMAND, RESOURCES, current, flow_complete, permits, sources
from .planning.mining_outposts import MiningOutpostPlanner


class MiningOutpostMixin:
    planner_type = MiningOutpostPlanner

    def __init__(self, backend, jev=None, **options):
        self._mining_outposts_enabled = True
        self._outpost_fault = False
        self._outpost_evidence = {}
        super().__init__(backend, jev, **options)
        native = getattr(backend, '_factory', None)
        if native is not None:
            from .backends import has_adapter
            from .backends.mining_outposts import MiningOutpostFactory
            if not has_adapter(native, MiningOutpostFactory):
                backend._factory = MiningOutpostFactory(native)
        elif getattr(backend, 'mining_outposts_supported', False) is not True:
            raise ValueError('Backend does not support mining-outpost evidence')

    def _observe(self, stage='observe'):
        snapshot = super()._observe(stage)
        try:
            rows = sources(snapshot)
            for resource, expected in self.memory.outpost_commitments.items():
                row = rows.get(resource)
                if (not row or row['state'] == 'proposed'
                        or any(row[key] != expected[key] for key in ('layout', 'surface_index', 'force_index', 'steps'))
                        or any(row['parts'].get(part) != paid for part, paid in expected['parts'].items())
                        or expected['flow'] and row['flow'] != expected['flow']):
                    raise ValueError('Mining-outpost ownership disappeared or regressed')
            for resource, row in rows.items():
                if not current(row, snapshot):
                    raise ValueError('Mining-outpost identity or flow requires reconciliation')
                if row['flow'] and not flow_complete(resource, row['layout'], snapshot):
                    raise ValueError('Invalid mining-outpost flow certificate')
                if row['state'] == 'proposed':
                    continue
                expected = self.memory.outpost_commitments.get(resource)
                if expected is None or set(row['parts']) != set(expected['parts']):
                    plan = self.memory.active_plan or {}
                    steps = plan.get('steps', [])
                    step = steps[self.memory.step_index] if self.memory.step_index < len(steps) else {}
                    params = step.get('parameters') or {}
                    new = set(row['parts']) - set(expected['parts'] if expected else {})
                    if (not self.memory.pending or step.get('action') != COMMAND
                            or params.get('resource') != resource or params.get('layout') != row['layout']
                            or any(part != params.get('part') or row['parts'][part]['receipt'] != params.get('receipt')
                                   for part in new)):
                        raise ValueError('Untracked mining-outpost construction; preserve existing ownership')
                self.memory.outpost_commitments[resource] = {
                    key: deepcopy(row[key]) for key in ('layout', 'surface_index', 'force_index', 'steps', 'parts', 'flow')}
        except (ValueError, KeyError, TypeError, AttributeError):
            self._outpost_fault = True
            self.memory.status, self.memory.reason = 'uncertain', 'Mining-outpost evidence invalid; preserve pending work'
        self._outpost_evidence = deepcopy(snapshot.factory.get('mining_outposts', {}))
        self._save()
        return snapshot

    def _execution_barrier(self, snapshot):
        return self._outpost_fault or super()._execution_barrier(snapshot)

    def _step_allowed(self, step, snapshot):
        return (not self._execution_barrier(snapshot)
                and permits(step.action, step.parameters or {}, snapshot)
                and super()._step_allowed(step, snapshot))

    def _record_extras(self):
        return {**super()._record_extras(), 'mining_outposts': True,
                'mining_outpost_evidence': deepcopy(self._outpost_evidence)}

    def _compile_candidates(self, snapshot):
        original, blocker = super()._compile_candidates(snapshot)
        if self.memory.active_goal != 'rocket_launch':
            return original, blocker
        boiler = snapshot.factory.get('entities', {}).get('utility:boiler', {})
        if boiler and boiler.get('fuel', {}).get('coal', 0) < 5:
            return original, blocker
        planner = self.planner_type(self.catalog, snapshot, self.memory.active_goal)
        for row in sources(snapshot).values():
            part = row['parts'].get('drill')
            if not part or not row['topology'] or not row['remaining']:
                continue
            fuel = snapshot.factory['entities'][part['role']].get('fuel', {}).get('coal', 0)
            if fuel < 2:
                count = min(20 if row['flow'] else 5, self.catalog.stack_sizes.get('coal', 50)) - fuel
                plan = planner._acquire_outpost('coal', count, ()) or planner._transfer(part['role'], 'coal', count)
                if self._step_allowed(plan.steps[0], snapshot):
                    return [plan], ''
        return [plan for plan in original if self._step_allowed(plan.steps[0], snapshot)], blocker


def outpost_loop_type(base):
    """Explicit extension: old readers reject it instead of losing paid identities."""
    if not hasattr(base.memory_type, 'input_routes_schema'):
        raise ValueError('Mining outposts require the input-route controller')

    @dataclass
    class OutpostMemory(base.memory_type):
        outposts_schema: int = 1
        outpost_commitments: dict = field(default_factory=dict)

        @classmethod
        def load(cls, path, session_id, target):
            data = json.loads(path.read_text(encoding='utf-8'))
            keys = {'outposts_schema', 'outpost_commitments'}
            legacy = not keys.intersection(data)
            if not legacy and not keys <= data.keys():
                raise ValueError('Incomplete mining-outpost checkpoint extension')
            memory = super().load(path, session_id, target)
            if legacy:
                if (memory.status != 'running' or memory.active_plan or memory.pending or memory.reservations
                        or getattr(memory, 'background_job', None)):
                    raise ValueError('Enable mining outposts only at an idle, reconciled controller boundary')
                memory.event('mining_outposts_enabled', tick=memory.last_tick,
                             reason='explicit_capability_at_idle_boundary')
            if (type(memory.outposts_schema) is not int or memory.outposts_schema != 1
                    or not isinstance(memory.outpost_commitments, dict)
                    or not set(memory.outpost_commitments).issubset(RESOURCES)):
                raise ValueError('Invalid mining-outpost checkpoint extension')
            # Validate frozen evidence without contacting the world or writing the checkpoint.
            rows, entities = {}, {}
            for resource, entry in memory.outpost_commitments.items():
                if not isinstance(entry, dict) or set(entry) != {'layout', 'surface_index', 'force_index', 'steps', 'parts', 'flow'}:
                    raise ValueError('Invalid mining-outpost commitment')
                rows[resource] = dict(entry, resource=resource, remaining=0,
                                     topology=bool(entry['flow']), state='ready' if entry['flow'] else 'building')
            view = SimpleNamespace(session_id=session_id, tick=max(0, memory.last_tick), factory={
                'mining_outposts': {'protocol': 1, 'session_id': session_id, 'tick': max(0, memory.last_tick), 'sources': rows},
                'entities': entities})
            sources(view)
            for row in rows.values():
                for spec in row['steps']:
                    part = row['parts'].get(spec['part'])
                    if part:
                        entities[part['role']] = dict(name=spec['name'], position=spec['position'], unit_number=part['unit_number'])
                if row['flow'] and not flow_complete(row['resource'], row['layout'], view):
                    raise ValueError('Invalid retained mining-outpost flow proof')
            return memory


    return type('MiningOutpostLoop', (MiningOutpostMixin, base), {'memory_type': OutpostMemory, '__module__': __name__})
