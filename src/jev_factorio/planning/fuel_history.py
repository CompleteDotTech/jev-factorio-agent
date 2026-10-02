"""Bounded observation-only fuel depletion estimates, never action authority.

Inventory deltas are a depletion proxy, not an attributed native burn counter.
They may miss simultaneous insertions/consumption. Refills, missing identities,
ambiguous mutations, clock gaps and restarts discard the estimate. Current stock,
reservations, receipt checks and native preconditions still own every transfer.
"""
from __future__ import annotations

import math
from collections import deque

MAX_ENTITIES = 512
MAX_CONSUMERS = 64
MAX_SAMPLES = 17
MIN_SAMPLE_TICKS = 120
MIN_SPAN_TICKS = 600
MAX_GAP_TICKS = 7200
BASIS = 'observed_inventory_depletion_proxy_not_attributed_native_burn'
BURNERS = {'burner-inserter', 'burner-mining-drill'}


def context(snapshot) -> tuple | None:
    runtime = snapshot.factory.get('acceptance_runtime', {})
    if (not isinstance(runtime, dict) or type(runtime.get('schema')) is not int
            or runtime['schema'] != 1 or runtime.get('session_id') != snapshot.session_id
            or not snapshot.session_id or type(snapshot.tick) is not int or snapshot.tick < 0
            or any(type(runtime.get(k)) is not int or runtime[k] <= 0
                   for k in ('actor_unit', 'player_index', 'surface_index', 'force_index'))):
        return None
    return (snapshot.session_id, snapshot.world_kind, snapshot.game_version,
            *(runtime[k] for k in ('actor_unit', 'player_index', 'surface_index', 'force_index')))


def _owned_samples(snapshot) -> dict | None:
    entities = snapshot.factory.get('entities')
    if not isinstance(entities, dict) or len(entities) > MAX_ENTITIES:
        return None
    result = {}
    for role, machine in entities.items():
        if not isinstance(machine, dict):
            continue
        burner = machine.get('name') in BURNERS
        # The boiler is tracked only through its canonical campaign role. Do
        # not let another boiler-shaped entity or an alias add a second sample
        # to the power-service estimate.
        boiler = role == 'utility:boiler' and machine.get('name') == 'boiler'
        if not burner and not boiler:
            continue
        unit, fuel = machine.get('unit_number'), machine.get('fuel')
        point = machine.get('position')
        if (type(unit) is not int or unit <= 0 or not isinstance(fuel, dict)
                or type(fuel.get('coal', 0)) is not int or fuel.get('coal', 0) < 0
                or not isinstance(point, dict)
                or any(type(point.get(k)) not in {int, float} or not math.isfinite(point[k])
                       for k in ('x', 'y'))):
            return None
        value = (machine['name'], point['x'], point['y'], fuel.get('coal', 0))
        if unit in result and result[unit] != value:
            return None  # An alias is not a second independent measurement.
        result[unit] = value
        if len(result) > MAX_CONSUMERS:
            return None
    return result


class FuelHistory:
    """Process-local, identity-bound, bounded context; no checkpoint or native calls."""
    def __init__(self) -> None:
        self._context: tuple | None = None
        self._tick: int | None = None
        self._samples: dict[int, tuple[tuple, deque]] = {}

    def clear(self) -> None:
        self._context, self._tick = None, None
        self._samples.clear()

    def observe(self, snapshot, *, ambiguous: bool = False) -> dict:
        identity = context(snapshot)
        values = _owned_samples(snapshot) if identity is not None else None
        rates = {}
        if (ambiguous or values is None or self._context != identity
                or self._tick is not None and not 0 <= snapshot.tick - self._tick <= MAX_GAP_TICKS):
            self.clear()
        if values is not None and not ambiguous:
            self._context, self._tick = identity, snapshot.tick
            self._samples = {unit: row for unit, row in self._samples.items() if unit in values}
            for unit, value in values.items():
                fingerprint, fuel = value[:3], value[3]
                previous = self._samples.get(unit)
                samples = previous[1] if previous and previous[0] == fingerprint else deque(maxlen=MAX_SAMPLES)
                if samples and (fuel > samples[-1][1] or fuel == 0
                                or snapshot.tick == samples[-1][0] and fuel != samples[-1][1]):
                    samples.clear()  # Refill/reset/censored depletion: start a new window.
                if not samples or snapshot.tick - samples[-1][0] >= MIN_SAMPLE_TICKS:
                    samples.append((snapshot.tick, fuel))
                self._samples[unit] = (fingerprint, samples)
                span = samples[-1][0] - samples[0][0]
                drops = sum(left[1] > right[1] for left, right in zip(samples, list(samples)[1:]))
                if span >= MIN_SPAN_TICKS and drops >= 2 and fuel > 0:
                    rates[unit] = {'rate_per_tick': (samples[0][1] - samples[-1][1]) / span,
                                   'span_ticks': span, 'samples': len(samples)}
        snapshot._fuel_service_history = {'schema': 1, 'context': identity,
                                          'observed_tick': snapshot.tick,
                                          'basis': BASIS, 'rates': rates}
        return {'schema': 1, 'basis': BASIS, 'eligible_consumers': len(rates),
                'sampled_consumers': len(self._samples), 'ambiguous_reset': ambiguous}


def estimated_rate(snapshot, unit: int) -> float | None:
    evidence = getattr(snapshot, '_fuel_service_history', {})
    identity = context(snapshot)
    if (identity is None or not isinstance(evidence, dict) or evidence.get('schema') != 1
            or evidence.get('context') != identity or evidence.get('observed_tick') != snapshot.tick
            or evidence.get('basis') != BASIS or not isinstance(evidence.get('rates'), dict)):
        return None
    row = evidence['rates'].get(unit)
    if (not isinstance(row, dict) or type(row.get('samples')) is not int
            or not 3 <= row['samples'] <= MAX_SAMPLES
            or type(row.get('span_ticks')) is not int
            or not MIN_SPAN_TICKS <= row['span_ticks'] <= MAX_GAP_TICKS * MAX_SAMPLES
            or type(row.get('rate_per_tick')) not in {int, float}
            or not math.isfinite(row['rate_per_tick']) or not 0 < row['rate_per_tick'] <= 1):
        return None
    return float(row['rate_per_tick'])
