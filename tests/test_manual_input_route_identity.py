"""Actual053 post-harvest replay; no native dispatch or model queries."""
import json
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path

from jev_factorio.judgments import question_batch, select_plan
from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.decision_support import scheduling_context
from jev_factorio.planning.mining_outposts import MiningOutpostPlanner
from jev_factorio.planning.output_buffers import OutputBufferPlanner
from jev_factorio.skills import Plan
from jev_factorio.state import GameSnapshot


def capture():
    folder = Path(__file__).parent / 'fixtures'
    data = json.loads((folder / 'native053-post-harvest-input-frontier.json').read_text())
    catalog_data = json.loads((folder / 'native049-proposed-input-kit.json').read_text())
    state = GameSnapshot(**deepcopy(data['snapshot']))
    # The native event records an accepted observation. Private markers are
    # rehydrated for this explicit offline replay, not newly verified live.
    state._atomic_inventory_verified = state._coherent_observation_verified = (state.session_id, state.tick)
    catalog = Catalog.from_dict(catalog_data['catalog'])
    assert state.game_version == catalog.version == '2.0.77'
    assert state.inventory['iron-ore'] == 20 and data['provenance']['offline_only']
    return state, catalog, data


def test_actual053_distinct_manual_delivery_survives_primary_id_collision():
    state, catalog, data = capture()
    before = deepcopy(asdict(state))
    plans = MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()
    assert len(plans) == 2
    kit, manual = plans
    assert kit.id == 'factory:factory_insert:recipe:iron-plate'
    assert kit.steps[0].parameters['quantity'] == 1
    assert manual.steps[0].parameters['quantity'] == 20
    assert manual.id.startswith(kit.id + ':manual:') and manual.id != kit.id
    assert kit.steps[0].costs == {'iron-ore': 1}
    assert manual.steps[0].costs == {'iron-ore': 20}
    assert all(plan.steps[0].allowed(state) and not plan.steps[0].satisfied(state) for plan in plans)
    assert [plan.to_dict() for plan in plans] == [plan.to_dict() for plan in MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()]
    assert Plan.from_dict(manual.to_dict()) == manual
    assert manual.materials['local_objective']['item'] == 'automation-science-pack'
    assert 'input_route_kit_prerequisite' not in manual.materials
    context = scheduling_context(state, catalog, plans, 'rocket_launch')
    proof = context['candidate_evidence'][manual.id]['recipe_input_transfer_start_evidence']
    assert proof['paid_quantity_to_transfer'] == 20
    assert proof['ingredient_in_inventory_now'] == 20
    assert proof['owned_source_role'] == 'recipe:iron-plate' and proof['owned_source_unit'] == 2547
    assert proof['native_transfer_and_later_output_require_verification'] is True
    request, questions, offered = question_batch({**deepcopy(data['historical_state']), **context}, plans, max_bytes=48000)
    assert offered == plans and set(request['candidate_evidence']) == {kit.id, manual.id}
    assert len(json.dumps({'state': request, 'questions': questions}, ensure_ascii=False, allow_nan=False).encode()) <= 48000
    assert asdict(state) == before

    class HistoricalClient:
        def evaluate(self, state, questions):
            return deepcopy(data['historical_answers'])

    # The recorded response judged the old single kit candidate, never the new
    # manual alternative. Its original rejection remains unchanged.
    result = select_plan(HistoricalClient(), data['historical_state'], [kit])
    assert result.plan_id is None and result.answers == data['historical_answers']


def test_identical_work_deduplicates_despite_attempt_receipt(monkeypatch):
    state, catalog, _ = capture()
    kit, manual = MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()
    duplicate = replace(kit, steps=(replace(kit.steps[0], parameters={**kit.steps[0].parameters, 'receipt': 'another-attempt'}),))
    monkeypatch.setattr(OutputBufferPlanner, 'candidates', lambda self: [duplicate] if getattr(self, '_defer_proposed_input_route', False) else [kit])
    assert MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates() == [kit]


def test_distinct_manual_id_is_receipt_independent_and_deduplicated(monkeypatch):
    state, catalog, _ = capture()
    kit, manual = MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()
    manual = replace(manual, id=kit.id)
    changed_receipt = replace(manual, steps=(replace(manual.steps[0], parameters={**manual.steps[0].parameters, 'receipt': 'later-observation'}),))
    monkeypatch.setattr(OutputBufferPlanner, 'candidates', lambda self: [manual, changed_receipt] if getattr(self, '_defer_proposed_input_route', False) else [kit])
    result = MiningOutpostPlanner(catalog, state, 'rocket_launch', max_candidates=8).candidates()
    assert len(result) == 2 and result[0].id == kit.id
    expected = result[1].id
    monkeypatch.setattr(OutputBufferPlanner, 'candidates', lambda self: [changed_receipt] if getattr(self, '_defer_proposed_input_route', False) else [kit])
    assert MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()[1].id == expected


def test_collision_does_not_raise_existing_candidate_budget():
    state, catalog, _ = capture()
    assert len(MiningOutpostPlanner(catalog, state, 'rocket_launch', max_candidates=1).candidates()) == 1


def test_owned_building_route_stays_serial():
    state, catalog, _ = capture()
    state.factory['input_routes']['sources']['recipe:iron-plate']['state'] = 'building'
    plans = MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()
    assert len(plans) == 1 and ':manual:' not in plans[0].id


def test_manual_identity_does_not_bypass_current_inventory_or_source_ownership():
    state, catalog, _ = capture()
    kit, manual = MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()
    state.inventory['iron-ore'] = 19
    assert kit.steps[0].allowed(state) and not manual.steps[0].allowed(state)
    state.factory['entities']['recipe:iron-plate']['unit_number'] += 1
    import pytest
    with pytest.raises(ValueError):
        MiningOutpostPlanner(catalog, state, 'rocket_launch').candidates()
