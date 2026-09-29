"""Issue #94 benchmark keeps diagnostic drift separate from game-fact drift."""
from copy import deepcopy

from benchmarks.benchmark_atomic_observation import benchmark, game_facts


def test_atomic_benchmark_still_matches_current_game_facts():
    report = benchmark(20)
    assert report['matching_game_facts'] is True
    assert report['native_game_executed'] is False
    assert report['arms']['legacy_v1']['anchor_diagnostics'] is None
    assert report['arms']['atomic_v2']['anchor_diagnostics']['oil']['radius'] == 256
    assert report['diagnostics_excluded_from_fact_equality'] == [
        'observation_snapshot_schema', 'observation_query_bounds',
        'observation_anchor_diagnostics']


def test_fact_projection_preserves_inventory_and_receipts():
    value = {'inventory': {'coal': 8}, 'factory': {
        'receipts': {'paid-1': {'quantity': 1}},
        'observation_anchor_diagnostics': {'oil': {'radius': 256}}}}
    projected = game_facts(value)
    assert projected['factory'] == {'receipts': {'paid-1': {'quantity': 1}}}
    assert value['factory']['observation_anchor_diagnostics']['oil']['radius'] == 256
    changed = deepcopy(value)
    changed['factory']['receipts']['paid-1']['quantity'] = 2
    assert game_facts(changed) != projected
    changed = deepcopy(value)
    changed['inventory']['coal'] = 7
    assert game_facts(changed) != projected
