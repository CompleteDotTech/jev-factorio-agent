import json

import pytest

from jev_factorio.coal_supply import intents
from jev_factorio.supervisor import Supervisor, SupervisorConfig
from jev_factorio.treatment import SCHEMA, SCHEMA_V2


def make_supervisor(tmp_path, treatment_path):
    checkpoint = tmp_path / 'controller.json'
    checkpoint.write_text(json.dumps({'session_id': 'existing', 'target': 'rocket_launch',
                                      'status': 'running', 'pending': None}))
    state_dir = tmp_path / 'state'
    state_dir.mkdir(exist_ok=True)
    config = SupervisorConfig(state_dir=state_dir, checkpoint=checkpoint,
        session_id='existing', started_at=1000, repair_command=['repair'], cwd=tmp_path,
        factory_scheduling='ready-work', production_treatment=treatment_path)
    return Supervisor(config, clock=lambda: 1000, sleep=lambda _: None,
                      popen=lambda *a, **kw: None)


def test_supervisor_refuses_changed_immutable_treatment(tmp_path):
    path = tmp_path / 'treatment.json'
    content = {'schema': SCHEMA, 'solid_intents': intents(['a', 'b']),
               'coal_targets': ['a', 'b'], 'solid_science_policy': False,
               'coal_kit_policy': True}
    path.write_text(json.dumps(content))
    owner = make_supervisor(tmp_path, path)
    owner.initialize(record_only=True)
    assert '--production-treatment' in owner.gameplay_command()
    content['coal_kit_policy'] = False
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match='changed'):
        owner.gameplay_command()
    with pytest.raises(ValueError, match='cannot be changed'):
        owner.initialize(record_only=True)


def test_supervisor_pins_economic_admission_treatment_version(tmp_path):
    path = tmp_path / 'treatment.json'
    content = {'schema': SCHEMA_V2, 'solid_intents': intents(['a', 'b']),
               'coal_targets': ['a', 'b'], 'solid_science_policy': False,
               'coal_kit_policy': True, 'coal_economic_admission': True}
    path.write_text(json.dumps(content))
    owner = make_supervisor(tmp_path, path)
    owner.initialize(record_only=True)
    owner.gameplay_command()
    content.pop('coal_economic_admission')
    content['schema'] = SCHEMA
    path.write_text(json.dumps(content))
    with pytest.raises(ValueError, match='changed'):
        owner.gameplay_command()
