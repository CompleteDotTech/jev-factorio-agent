"""Additive construction and bounded trial supply over existing native contracts."""
from __future__ import annotations

from dataclasses import replace
import math

from .. import successors
from ..input_routes import sources as input_sources
from ..output_buffers import sources as output_sources, flow_complete as output_complete
from ..production_sites import sources as site_sources
from .input_routes import InputRoutePlanner


class SuccessorPlanner(InputRoutePlanner):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._successor_acquiring = False

    def _need(self, item, amount, path=()):
        role = 'growth:' + item
        if (not self._successor_acquiring and not self.speculative and self.goal == 'rocket_launch'
                and role in successors.ROLES and self.snapshot.inventory.get(item, 0) < amount):
            row = successors.sources(self.snapshot).get(role)
            if row and successors.flowing(role, self.snapshot):
                preferred = successors.qualified(role, self.snapshot)
                output = output_sources(self.snapshot)[role]
                available = self.entities[output['chest_role']].get('output', {}).get(item, 0)
                allowance = 50 if preferred else min(50, successors.TRIAL_LIMIT - row['trial_collected'])
                missing = math.ceil(amount - self.snapshot.inventory.get(item, 0))
                if available and allowance > 0:
                    if self.focus is None:
                        self._set_focus(item, amount)
                    plan = self._transfer(output['chest_role'], item, min(missing, available, allowance), extracting=True)
                    return replace(plan, materials={**(plan.materials or {}), 'successor_supply': {
                        'source': role, 'source_unit': row['source_unit'], 'observed_tick': self.snapshot.tick,
                        'kind': 'qualified' if preferred else 'bounded_trial',
                        'qualification': row['qualification']}})
                # An exhausted or empty successor does not hide the predecessor.
                # Native route maintenance is provided by the composed controller.
        return super()._need(item, amount, path)

    def acquire(self, item, amount):
        self._successor_acquiring = True
        previous = getattr(self, '_economic_acquiring', False)
        self._economic_acquiring = True
        self.allow_service_visits = False
        try:
            return self._acquire(item, amount, ())
        finally:
            self._successor_acquiring = False
            self._economic_acquiring = previous

    def fuel(self, role, target=5):
        have = self.entities[role].get('fuel', {}).get('coal', 0)
        if have >= 2:
            return None
        count = target - have
        return self.acquire('coal', count) or self._transfer(role, 'coal', count)

    def continuation(self, role, anchor):
        site = site_sources(self.snapshot).get(role, {})
        if site.get('anchor') != anchor or site.get('state') not in {'proposed', 'owned'}:
            raise ValueError('Successor site no longer matches its committed anchor')
        if site['state'] == 'proposed':
            for item, count in sorted(successors.initial_kit(site, successors.ROLES[role]).items()):
                if self.snapshot.inventory.get(item, 0) < count:
                    plan = self.acquire(item, count)
                    if plan:
                        return plan
                    raise ValueError('No safe acquisition path for successor kit')
            return self._plan('factory_place', 'machine', parameters={
                'role': role, 'name': 'stone-furnace', 'anchor': anchor},
                costs={'stone-furnace': 1}, timeout=18000, identity='successor:' + anchor,
                description='Place a paid, separately owned successor furnace; retain its predecessor')
        output = output_sources(self.snapshot).get(role)
        if not output:
            raise ValueError('Successor output layout is unavailable')
        if len(output['parts']) < 2:
            return self._buffer(output, 10, ())
        arm = output['parts']['inserter']['role']
        fuel = self.fuel(arm) or self.fuel(role)
        if fuel:
            return fuel
        if not output_complete(role, output['layout'], self.snapshot):
            native = successors.sources(self.snapshot)[role]
            count = 10 - native['seeded']
            if count:
                ore = successors.ROLES[role]
                return self.acquire(ore, count) or self._transfer(role, ore, count)
            return self._wait('buffer_flow', output['layout'], 3, role, timeout=18000,
                              identity='successor-output:' + anchor)
        route = input_sources(self.snapshot).get(role)
        if not route:
            return self._wait('machine_output', site.get('item', role[7:]), 10, output['chest_role'],
                              timeout=1800, identity='successor-route-survey:' + anchor)
        return self._route(route, ())


def marked(plan, role, site, snapshot):
    predecessor = 'recipe:' + role[7:]
    marker = {'source': role, 'anchor': site['anchor'],
              'predecessor_unit': snapshot.factory['entities'][predecessor]['unit_number'],
              'observed_tick': snapshot.tick}
    return replace(plan, id='successor:' + role + ':' + plan.id,
                   materials={**(plan.materials or {}), successors.MARKER: marker})
