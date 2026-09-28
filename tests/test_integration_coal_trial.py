from copy import deepcopy

import pytest

from jev_factorio import coal_supply
from jev_factorio.integration_evidence import TRIAL_SCHEMA_V2, validate_trial
from jev_factorio.treatment import SCHEMA, digest
from integration_evidence_fixtures import evidence


def test_complete_trial_binds_exact_coal_consumers():
    _, trial, _, _ = evidence()
    value = deepcopy(trial)
    value['schema'] = TRIAL_SCHEMA_V2
    value['coal_targets'] = [value['solid_intents'][0]['target'], value['solid_intents'][1]['target']]
    value['solid_intents'][:2] = coal_supply.intents(value['coal_targets'])
    value['configuration'].update(coal_supply=True, coal_kit_policy=True)
    value['treatment_sha256'] = digest({'schema': SCHEMA, 'solid_intents': value['solid_intents'],
        'coal_targets': value['coal_targets'], 'solid_science_policy': False, 'coal_kit_policy': True})
    value['initial_checkpoint_sha256'] = '0' * 64
    value['vm_uuid'] = 'isolated-vm'
    value['production_vm_uuid'] = 'production-vm'
    validate_trial(value)
    bad = deepcopy(value)
    bad['coal_targets'].reverse()
    with pytest.raises(ValueError, match='digest'):
        validate_trial(bad)
    bad = deepcopy(value)
    bad['solid_intents'][0]['source'] = 'unowned-stocked-chest'
    with pytest.raises(ValueError, match='corridor'):
        validate_trial(bad)
    bad = deepcopy(value)
    del bad['configuration']['coal_supply']
    with pytest.raises(ValueError, match='configuration'):
        validate_trial(bad)
