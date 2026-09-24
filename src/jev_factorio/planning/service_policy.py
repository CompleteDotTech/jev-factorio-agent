"""Snapshot-local service admission, never a path promise or inventory authority."""
from __future__ import annotations

import math
from collections import Counter

from .scheduling import SERVICE_TICKS, TRAVEL_TICKS_PER_TILE, research_schedule

MAX_EXTRA_TICKS = 900
MAX_LEG_TILES = 8
MAX_DEADLINE_ENTITIES = 64


def position(value):
    if isinstance(value, dict):
        value = [value.get('x'), value.get('y')]
    if (isinstance(value, (tuple, list)) and len(value) == 2
            and all(type(v) in {int, float} and math.isfinite(v) for v in value)):
        return tuple(value)
    return None


def carried_stock(planner) -> dict:
    """Intersect current observed stock with the planner's reservation-aware ledger.

    Collections and acknowledged craft output never add to this budget. Missing
    ledger evidence cannot authorize an extra delivery. The original first action
    still uses the ordinary controller's fresh preconditions. The ledger uses
    floats; rounding down never borrows a fractional reserved item.
    """
    carried = getattr(getattr(planner, 'ledger', None), 'carried', {})
    return {item: math.floor(min(count, carried.get(item, 0)))
            for item, count in planner.snapshot.inventory.items()
            if all(type(v) in {int, float} and math.isfinite(v) and v >= 0
                   for v in (count, carried.get(item, 0)))}


class ServiceBudget:
    """Keep the original action first; limit only optional appended transfers."""
    def __init__(self, planner, first, cell):
        self.snapshot = planner.snapshot
        self.entities = self.snapshot.factory.get('entities', {})
        self.first_role = first.parameters['role']
        self.origin = position(self.entities.get(self.first_role, {}).get('position'))
        player = position(self.snapshot.player_position)
        self.first_ticks = None if player is None or self.origin is None else (
            math.ceil(sum(abs(a - b) for a, b in zip(player, self.origin)) * TRAVEL_TICKS_PER_TILE)
            + SERVICE_TICKS)
        self.extra_ticks = 0
        self.rejections = Counter()
        self.deadline = None
        self.unknown_deadline = False
        try:
            self.science = research_schedule(self.snapshot, planner.catalog)
        except (ValueError, KeyError, TypeError, ArithmeticError):
            self.science = []
            self.unknown_deadline = True
        if self.snapshot.factory.get('research') and not self.science:
            self.unknown_deadline = True
        for row in self.science:
            if row['amount'] and self.first_role != 'utility:lab':
                if row['deadline_tick'] is None:
                    self.unknown_deadline = True
                else:
                    self.deadline = min(self.deadline, row['deadline_tick']) if self.deadline is not None else row['deadline_tick']
        self.other_emergency = len(self.entities) > MAX_DEADLINE_ENTITIES
        for role, machine in list(sorted(self.entities.items()))[:MAX_DEADLINE_ENTITIES]:
            fuel = machine.get('fuel', {}).get('coal')
            burner = planner.catalog.machines.get(machine.get('name'), {}).get('burner')
            if (role not in cell and (role == 'utility:boiler' or burner and machine.get('crafting'))
                    and type(fuel) in {int, float} and math.isfinite(fuel) and fuel < 2):
                self.other_emergency = True

    def admit(self, step) -> bool:
        target = position(self.entities.get(step.parameters['role'], {}).get('position'))
        reason = None
        if self.origin is None or target is None or self.first_ticks is None:
            reason = 'unknown_geometry'
        elif self.other_emergency:
            reason = 'other_cell_emergency'
        elif self.unknown_deadline and self.first_role != 'utility:lab':
            reason = 'unknown_research_deadline'
        else:
            distance = sum(abs(a - b) for a, b in zip(self.origin, target))
            duration = math.ceil(distance * TRAVEL_TICKS_PER_TILE) + SERVICE_TICKS
            proposed = self.extra_ticks + duration
            if distance > MAX_LEG_TILES:
                reason = 'service_leg_budget'
            elif proposed > MAX_EXTRA_TICKS:
                reason = 'service_time_budget'
            elif (self.deadline is not None
                    and self.snapshot.tick + self.first_ticks + proposed >= self.deadline):
                reason = 'research_refill_deadline'
            if reason is None:
                self.origin, self.extra_ticks = target, proposed
                return True
        self.rejections[reason] += 1
        return False

    def summary(self) -> dict:
        return {'schema': 1, 'observed_tick': self.snapshot.tick,
                'scope': 'same_cell_paid_service', 'first_role': self.first_role,
                'first_ticks_estimate': self.first_ticks,
                'extra_ticks_estimate': self.extra_ticks,
                'max_extra_ticks': MAX_EXTRA_TICKS, 'max_leg_tiles': MAX_LEG_TILES,
                'research_deadline_tick': self.deadline,
                'rejections': dict(self.rejections),
                'estimate_basis': 'Manhattan_distance_and_declared_policy_not_native_timing',
                'collections_are_spendable': False}
