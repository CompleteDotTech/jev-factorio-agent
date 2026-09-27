"""The production acceptance schema must not silently erase experimental transport."""
from collections import Counter
from copy import deepcopy

import pytest

from jev_factorio.acceptance_capture import project_record
from jev_factorio.research_log import Redactor


def record():
    return {'state': {'factory': {}}, 'after_state': {'factory': {}},
            'acceptance_configuration': {'factory_scheduling': 'ready-work'}}


@pytest.mark.parametrize('field', ['solid_routes', 'solid_route_evidence', 'solid_route_fault'])
def test_capture_rejects_experimental_top_level_evidence(field):
    row = record()
    row[field] = {} if field.endswith('evidence') else True
    with pytest.raises(ValueError, match='solid-route'):
        project_record(row, Redactor({}), Counter())


@pytest.mark.parametrize('label', ['state', 'after_state'])
def test_capture_rejects_experimental_native_envelope(label):
    row = record()
    row[label]['factory']['solid_routes'] = {}
    with pytest.raises(ValueError, match='solid-route'):
        project_record(row, Redactor({}), Counter())


@pytest.mark.parametrize('label', ['acceptance_configuration', 'campaign_treatment'])
def test_capture_rejects_undeclared_treatment_field(label):
    row = record()
    row[label] = {'solid_routes': True}
    with pytest.raises(ValueError, match='solid-route'):
        project_record(row, Redactor({}), Counter())


def test_capture_legacy_projection_unchanged_and_input_not_modified():
    row = record()
    initial = deepcopy(row)
    assert project_record(row, Redactor({}), Counter())['state'] == {'factory': {}}
    assert row == initial
