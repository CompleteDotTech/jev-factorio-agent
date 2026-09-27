"""A new commitment cannot downgrade its proof by omitting the selected step."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio.integration_evidence import analyze_rows
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_funding_evidence import funded_evidence, event
from test_solid_kit_acquisition import kit_loop


@pytest.mark.parametrize('erase_effect', [False, True])
def test_dispatched_commit_cannot_omit_step_to_bypass_effect_check(tmp_path, erase_effect):
    loop, _ = kit_loop(tmp_path)
    loop._observe()
    initial = asdict(loop.memory)
    record = loop.step()
    final = asdict(loop.memory)
    assert not funding_history_issues(initial, [record], final)
    commit = next(value for value in record['history'] if value['kind'] == 'solid_kit_committed')
    step = commit.pop('step')
    if erase_effect:
        record['after_state']['inventory'][step['item']] = record['state']['inventory'].get(step['item'], 0)
    assert funding_history_issues(initial, [record], final)


def test_old_step_less_initial_history_is_not_a_new_commit():
    data = funded_evidence()
    records, _, initial, _ = data
    old = event('solid_kit_committed', initial['solid_funding'], initial['last_tick'])
    old.pop('step')
    initial['history'] = [deepcopy(old)]
    for record in records: record['history'] = [deepcopy(old)]
    data[3]['history'] = deepcopy(records[-1]['history'])
    result = analyze_rows(*data)
    assert result['measurement_checks_passed'], result['issues']
