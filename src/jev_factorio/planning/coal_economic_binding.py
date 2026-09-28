"""Reconcile a declared forecast with independently decoded native facts.

This is a source-only calculation boundary. A caller must separately retain the
actual query/response provenance, manual delivery receipts, acquisition proof,
connector ownership and an atomic first-payment observation. Neither a Python
object nor a positive forecast is native action authority.
"""
from __future__ import annotations

from ..coal_economic_observation import NativeEconomics
from .coal_economic_proof import EconomicInput, _hash, _integer, digest
from .coal_economics import evaluate


def evaluate_bound_forecast(native: NativeEconomics, declared: dict) -> dict:
    """Evaluate only a forecast whose observed terms exactly match typed facts."""
    if type(native) is not NativeEconomics or native.mutation_authorized is not False \
            or native.native_payback_proven is not False:
        raise ValueError('Unqualified native coal economics facts')
    _integer(native.actor_unit, 1)
    _hash(native.raw_sha256)
    evidence = EconomicInput.parse(declared)
    if (evidence.epoch != native.epoch or evidence.bundle_sha256 != native.bundle_sha256
            or evidence.power != native.power or len(evidence.sources) != len(native.sources)):
        raise ValueError('Coal forecast differs from native epoch, bundle or power')
    if tuple(row.target_role for row in native.sources) != tuple(sorted(row.target_role for row in native.sources)):
        raise ValueError('Native coal sources are unordered')
    for stated, observed in zip(evidence.sources, native.sources):
        exact = ('target_role', 'target_unit', 'layout', 'remaining_ore', 'ore_mining_ticks',
                 'consumer_max_joules_per_tick', 'consumer_stored_fuel_joules_lower',
                 'consumer_stored_fuel_joules_upper')
        if any(getattr(stated, field) != getattr(observed, field) for field in exact):
            raise ValueError('Coal forecast source differs from native facts')
        if stated.mining_speed_ppm != 500_000:
            raise ValueError('Coal mining rate differs from qualified native prototype')
    result = evaluate(evidence.to_dict())
    # Verify the canonical calculator cannot silently omit the native terms.
    if result['input_sha256'] != digest(evidence.to_dict()) or result['mutation_authorized'] is not False:
        raise ValueError('Coal forecast calculation is not canonical')
    return {'forecast': result, 'native_raw_sha256': native.raw_sha256,
            'native_actor_unit': native.actor_unit, 'mutation_authorized': False,
            'native_payback_proven': False}
