"""Opt-in intermediate chain diagnostics never certify material provenance."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from jev_factorio import integration_evidence as evidence
from jev_factorio.coal_supply import intents
from jev_factorio.solid_routes import commitment, current
from jev_factorio.treatment import SCHEMA, digest
from integration_evidence_fixtures import evidence as fixture


CHAIN = {'route': 'solid:1:2:iron-ore:input', 'producer_role': 'recipe:iron-plate',
         'producer_recipe': 'iron-plate', 'product_item': 'iron-plate',
         'consumer_role': 'recipe:automation-science-pack', 'consumer_unit': 22,
         'science_pack': 'automation-science-pack'}


def trial_v3():
    _, trial, _, _ = fixture()
    trial['schema'] = evidence.TRIAL_SCHEMA_V3
    trial['downstream_recipes'] = ['iron-plate']
    trial['downstream_chain'] = [deepcopy(CHAIN)]
    trial['coal_targets'] = [trial['solid_intents'][0]['target'], trial['solid_intents'][1]['target']]
    trial['solid_intents'][:2] = intents(trial['coal_targets'])
    trial['configuration'].update(coal_supply=True, coal_kit_policy=True)
    trial['treatment_sha256'] = digest({'schema': SCHEMA, 'solid_intents': trial['solid_intents'],
        'coal_targets': trial['coal_targets'], 'solid_science_policy': False, 'coal_kit_policy': True})
    trial['initial_checkpoint_sha256'] = '0' * 64
    trial['vm_uuid'] = 'isolated-vm'
    trial['production_vm_uuid'] = 'production-vm'
    return trial


def chain_inputs():
    route = {'route': CHAIN['route'], 'kind': 'downstream', 'target_unit': 11,
             'recipe': 'iron-plate', 'attributed_positive_boundaries': 3,
             'attributed_received': 4, 'first_positive_tick': 10,
             'target_first_products': 0, 'target_last_products': 2}
    observed_route = {'target': {'role': CHAIN['producer_role'], 'unit_number': 11}}
    rows = [{'after_state': {'factory': {'solid_routes': {'routes': {CHAIN['route']: observed_route}},
            'entities': {CHAIN['consumer_role']: {'unit_number': 22,
                'recipe': CHAIN['science_pack'], 'products_finished': products}}}}}
            for products in (0, 2)]
    receipts = {
        'extract-ingredient': {'role': CHAIN['producer_role'], 'unit_number': 11,
            'item': CHAIN['product_item'], 'extracting': True, 'quantity': 2, 'tick': 11},
        'insert-ingredient': {'role': CHAIN['consumer_role'], 'unit_number': 22,
            'item': CHAIN['product_item'], 'extracting': False, 'quantity': 2, 'tick': 12},
        'extract-pack': {'role': CHAIN['consumer_role'], 'unit_number': 22,
            'item': CHAIN['science_pack'], 'extracting': True, 'quantity': 1, 'tick': 13},
        'insert-lab': {'role': 'utility:lab', 'unit_number': 33,
            'item': CHAIN['science_pack'], 'extracting': False, 'quantity': 1, 'tick': 14},
    }
    return rows, {CHAIN['route']: route}, receipts


def diagnose(rows, routes, receipts, chain=CHAIN):
    return evidence._downstream_chain_diagnostic([chain], rows, routes, receipts, 33)


def test_v3_declares_bounded_intermediate_chain_without_changing_old_trial():
    evidence.validate_trial(trial_v3())
    _, old, _, _ = fixture()
    evidence.validate_trial(old)


@pytest.mark.parametrize('change', [
    lambda t: t['downstream_chain'][0].update(producer_recipe='copper-plate'),
    lambda t: t['downstream_chain'][0].update(science_pack='logistic-science-pack'),
    lambda t: t['downstream_chain'].append(deepcopy(t['downstream_chain'][0])),
    lambda t: t['downstream_chain'][0].update(consumer_unit=True),
])
def test_v3_rejects_unbound_or_duplicated_declaration(change):
    trial = trial_v3()
    change(trial)
    with pytest.raises(ValueError, match='downstream chain'):
        evidence.validate_trial(trial)


def test_complete_paid_sequence_is_diagnostic_correlation_only():
    value = diagnose(*chain_inputs())
    assert value['correlated_sequences'] == 1
    assert value['status_by_declared_order'] == ['correlated_paid_transfer_sequence']
    assert value['intermediate_provenance_qualified'] is False


@pytest.mark.parametrize('damage', [
    lambda rows, routes, receipts: rows[0].pop('after_state'),
    lambda rows, routes, receipts: rows[0]['after_state']['factory'].pop('solid_routes'),
    lambda rows, routes, receipts: rows[-1]['after_state']['factory']['entities'].pop(
        CHAIN['consumer_role']),
    lambda rows, routes, receipts: routes[CHAIN['route']].pop('recipe'),
])
def test_missing_or_malformed_observation_paths_fail_closed(damage):
    rows, routes, receipts = chain_inputs()
    damage(rows, routes, receipts)
    assert diagnose(rows, routes, receipts)['correlated_sequences'] == 0


def test_analyzer_keeps_intermediate_route_unqualified_with_sequence():
    rows, trial, initial, final = fixture()
    route = next(value for value in rows[0]['after_state']['factory']['solid_routes']['routes'].values()
                 if value['target']['inventory'] == 'input')
    chain = {**CHAIN, 'route': route['route'], 'producer_role': route['target']['role'],
             'consumer_role': 'fixture:science-assembler', 'consumer_unit': 2233}
    trial['schema'] = evidence.TRIAL_SCHEMA_V3
    trial['downstream_recipes'] = ['iron-plate']
    trial['downstream_chain'] = [chain]
    trial['coal_targets'] = [trial['solid_intents'][0]['target'], trial['solid_intents'][1]['target']]
    trial['solid_intents'][:2] = intents(trial['coal_targets'])
    trial['configuration'].update(coal_supply=True, coal_kit_policy=True)
    trial['treatment_sha256'] = digest({'schema': SCHEMA, 'solid_intents': trial['solid_intents'],
        'coal_targets': trial['coal_targets'], 'solid_science_policy': False, 'coal_kit_policy': True})
    trial['initial_checkpoint_sha256'] = '0' * 64
    trial['vm_uuid'], trial['production_vm_uuid'] = 'isolated-vm', 'production-vm'
    for checkpoint in (initial, final):
        checkpoint['solid_intents'] = trial['solid_intents']
        checkpoint['coal_targets'] = trial['coal_targets']
        checkpoint['coal_kit_policy'] = True
        checkpoint['coal_supply_schema'] = 1
        checkpoint['coal_epoch'] = dict(checkpoint['solid_epoch'])
        checkpoint['coal_commitments'] = {}
    for index, record in enumerate(rows):
        record['acceptance_configuration'].update(coal_supply=True, coal_kit_policy=True)
        record['coal_supply_fault'] = False
        for label in ('state', 'after_state'):
            state = record[label]
            state['factory']['coal_supply'] = {'protocol': 1, 'session_id': state['session_id'],
                'tick': state['tick'], 'actor_index': 1, 'surface_index': 1, 'force_index': 1,
                'targets': trial['coal_targets'], 'committed': False, 'sources': {},
                'reason': 'no_supported_bundle'}
            for coal_route in state['factory']['solid_routes']['routes'].values():
                if coal_route['target']['inventory'] != 'fuel':
                    continue
                old_role = coal_route['source']['role']
                new_role = 'coal:' + coal_route['target']['role'] + ':chest'
                coal_route['source']['role'] = new_role
                state['factory']['entities'][new_role] = state['factory']['entities'].pop(old_role)
            routed = state['factory']['solid_routes']['routes'][chain['route']]
            routed['target']['recipe'] = 'iron-plate'
            state['factory']['entities'][chain['producer_role']]['recipe'] = 'iron-plate'
            state['factory']['entities'][chain['consumer_role']] = {
                'name': 'assembling-machine-1', 'unit_number': chain['consumer_unit'],
                'recipe': chain['science_pack'], 'products_finished': index * 2}
            additions = {
                2: ('ingredient-out', chain['producer_role'], routed['target']['unit_number'],
                    'iron-plate', True, 2, 8200),
                3: ('ingredient-in', chain['consumer_role'], chain['consumer_unit'],
                    'iron-plate', False, 2, 11800),
                4: ('pack-out', chain['consumer_role'], chain['consumer_unit'],
                    chain['science_pack'], True, 2, 15400),
            }
            for step, (receipt_id, role, unit, item, extracting, qty, tick) in additions.items():
                if index >= step:
                    state['factory']['receipts'][receipt_id] = {
                        'role': role, 'unit_number': unit, 'item': item,
                        'extracting': extracting, 'quantity': qty, 'tick': tick}
    for checkpoint, state in ((initial, rows[0]['state']), (final, rows[-1]['after_state'])):
        checkpoint['solid_commitments'] = {key: commitment(value) for key, value in
            state['factory']['solid_routes']['routes'].items()}
    for record in rows:
        for label in ('state', 'after_state'):
            state = record[label]
            view = SimpleNamespace(factory=state['factory'])
            for key, value in state['factory']['solid_routes']['routes'].items():
                assert current(value, view), (label, state['tick'], key)
    result = evidence.analyze_rows(rows, trial, initial, final)
    assert result['integrity_checks_passed'], result['issues']
    diagnostic = result['transport']['intermediate_chain_diagnostic']
    assert diagnostic['correlated_sequences'] == 1, diagnostic
    assert diagnostic['intermediate_provenance_qualified'] is False
    assert result['transport']['downstream_routes_with_flow_and_production'] == 0
    assert 'downstream_flow_and_production_not_measured' in result['outcome_gaps']


@pytest.mark.parametrize('break_witness', [
    lambda rows, routes, receipts: receipts['extract-ingredient'].update(unit_number=999),
    lambda rows, routes, receipts: receipts['insert-ingredient'].update(quantity=1),
    lambda rows, routes, receipts: receipts['extract-pack'].update(tick=9),
    lambda rows, routes, receipts: receipts['insert-lab'].update(unit_number=999),
    lambda rows, routes, receipts: rows[-1]['after_state']['factory']['entities'][
        CHAIN['consumer_role']].update(recipe='transport-belt'),
    lambda rows, routes, receipts: routes[CHAIN['route']].update(target_last_products=0),
    lambda rows, routes, receipts: routes[CHAIN['route']].update(first_positive_tick=15),
])
def test_missing_stale_or_wrong_owner_boundary_cannot_report_sequence(break_witness):
    rows, routes, receipts = chain_inputs()
    break_witness(rows, routes, receipts)
    assert diagnose(rows, routes, receipts)['correlated_sequences'] == 0
