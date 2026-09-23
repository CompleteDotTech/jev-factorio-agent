"""Add mining capacity for legacy manual cells, preserving those cells in place."""
from __future__ import annotations

import math
from dataclasses import replace

from ..mining_outposts import COMMAND, RESOURCES, flow_complete, remaining_kit, role, sources
from .input_routes import InputRoutePlanner


class MiningOutpostPlanner(InputRoutePlanner):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._outpost_acquiring = False

    def _acquire_outpost(self, item, amount, path):
        previous = self._outpost_acquiring
        self._outpost_acquiring = True
        try:
            return super()._need(item, amount, path)
        finally:
            self._outpost_acquiring = previous

    def _need(self, item, amount, path=()):
        if (item not in RESOURCES or self._outpost_acquiring or self.goal != 'rocket_launch'
                or self.snapshot.inventory.get(item, 0) >= amount):
            return super()._need(item, amount, path)
        row = sources(self.snapshot).get(item)
        if not row or row['state'] == 'fault':
            return super()._need(item, amount, path)
        if row['state'] == 'proposed':
            machine = self.entities.get(RESOURCES[item], {})
            direct = self.factory.get('input_routes', {}).get('sources', {}).get(RESOURCES[item])
            # Small bootstrap work and an available direct route take precedence.
            # Only invest for an established manually supplied producer.
            if (direct or machine.get('products_finished', 0) < 20
                    or amount - self.snapshot.inventory.get(item, 0) < 10):
                return super()._need(item, amount, path)
            # Collect already-paid legacy ore rather than hiding it behind an investment.
            if any(e.get('output', {}).get(item, 0) for e in self.entities.values()):
                return super()._need(item, amount, path)
        if self.focus is None:
            self._set_focus(item, amount)
        kit = remaining_kit(row)
        if kit:
            self._buffer_service = True  # Keep kit acquisition/construction on the primary path.
            for name, count in {**kit, 'coal': 5}.items():
                prerequisite = self._acquire_outpost(name, count, path)
                if prerequisite:
                    return prerequisite
            spec = next(s for s in row['steps'] if s['part'] not in row['parts'])
            return self._plan(COMMAND, 'outpost_component', parameters={
                'resource': item, 'layout': row['layout'], 'part': spec['part'],
                'receipt': f"{self.snapshot.tick}:{row['layout']}:{spec['part']}",
            }, costs={**kit, 'coal': 5}, timeout=18000,
                identity=f"{row['layout']}:{spec['part']}",
                description=f"Build paid {spec['name']} for {item} mining outpost; preserve existing furnace")
        if not row['topology']:
            self._buffer_service = True
            return self._wait('outpost_flow', row['layout'], 3, item, timeout=1800,
                              identity=f"commission:{row['layout']}")
        drill = self.entities[role(item, 'drill')]
        if row['remaining'] and drill.get('fuel', {}).get('coal', 0) < 2:
            self._buffer_service = True
            count = min(20 if row['flow'] else 5, self.catalog.stack_sizes.get('coal', 50)) - drill.get('fuel', {}).get('coal', 0)
            return self._acquire_outpost('coal', count, path) or self._transfer(role(item, 'drill'), 'coal', count)
        if not flow_complete(item, row['layout'], self.snapshot):
            self._buffer_service = True
            return self._wait('outpost_flow', row['layout'], 3, item, timeout=3600,
                              identity=f"commission:{row['layout']}")
        chest = role(item, 'chest')
        available = self.entities[chest].get('output', {}).get(item, 0)
        missing = math.ceil(amount - self.snapshot.inventory.get(item, 0))
        # Never wait beyond remaining native ore. An exhausted outpost stays owned;
        # a small tail is collectible, then ordinary observed gathering may resume.
        target = min(50, missing, available + row['remaining'])
        if available >= target and available:
            return self._transfer(chest, item, min(50, available, max(missing, self.collection_batch)), extracting=True)
        if row['remaining']:
            return self._wait('machine_output', item, target, chest, timeout=18000,
                              identity=f"outpost-collect:{row['layout']}:{target}")
        return super()._need(item, amount, path)

    def candidates(self):
        plans = super().candidates()
        evidence = {key: {name: row[name] for name in ('layout', 'state', 'remaining', 'topology', 'flow')}
                    for key, row in sources(self.snapshot).items()}
        return [replace(plan, materials={**(plan.materials or {}), 'mining_outposts': evidence,
                                        'ore_transport': 'bounded_manual_hauling'}) for plan in plans]
