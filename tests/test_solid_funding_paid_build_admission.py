"""All paid corridor components require admission and exact dispatch evidence."""
from copy import deepcopy
from dataclasses import asdict
import pytest
from jev_factorio.telemetry import fingerprint
from jev_factorio.planning import solid_investment
from jev_factorio.solid_funding_evidence import funding_history_issues
from test_solid_kit_acquisition import kit_loop
from solid_routes_fixtures import row


def complete_trace(tmp_path):
    loop, backend = kit_loop(tmp_path)
    loop._observe(); initial = asdict(loop.memory)
    records = []; captured = []
    for _ in range(20):
        records.append(loop.step())
        captured.append(asdict(loop.memory))
        assert records[-1]['verified']
        if row(backend.state)['state'] == 'ready': break
    final = asdict(loop.memory)
    assert not funding_history_issues(initial, records, final)
    return initial, records, final, captured


@pytest.mark.parametrize('component', [0, 2])
@pytest.mark.parametrize('change', ['future_marker', 'missing_marker', 'missing_admission', 'changed_catalog', 'funding'])
def test_paid_build_requires_live_policy_admission(tmp_path, component, change):
    initial, records, final, captured = complete_trace(tmp_path)
    record = [value for value in records if value['action'] == 'factory_solid_build'][component]
    key = record['decision']['plan_id']
    if change in {'future_marker', 'missing_marker'}:
        definition = None
        for history in [value['history'] for value in records] + [final['history']]:
            for event in history:
                if event.get('kind') == 'plan_committed' and event.get('plan') == key:
                    definition = event['definition']
                    if change == 'future_marker': definition['materials'][solid_investment.MARKER]['observed_tick'] = record['state']['tick'] + 100
                    else: definition['materials'].pop(solid_investment.MARKER, None)
        frontier = record['planning_diagnostics']['candidate_frontier']
        next(value for value in frontier if value['id'] == key)['sha256'] = fingerprint(definition)
    elif change == 'missing_admission': record['planning_diagnostics'].pop('solid_build_admission', None)
    else:
        admission = record['planning_diagnostics'].setdefault('solid_build_admission', {})
        if change == 'changed_catalog': admission.setdefault('acquisition', {})['catalog'] = {}
        else: admission['funding'] = {'unproven': True}
    assert funding_history_issues(initial, records, final)


def test_paid_growth_after_handoff_cannot_erase_the_build_attempt(tmp_path):
    initial, records, final, captured = complete_trace(tmp_path)
    removed = [value for value in records if value['action'] == 'factory_solid_build'][2]
    key = removed['decision']['plan_id']; attempt = removed['attempt_outcomes'][-1]['id']
    removed.update(action='observe', outcome='Observed state', verified=False, decision=None,
                   model_call=False, planning_diagnostics={})
    for value, saved in zip(records, captured):
        value['history'] = [event for event in saved['history'] if event.get('plan') != key][-8:]
        value['attempt_outcomes'] = [outcome for outcome in saved['attempt_outcomes'] if outcome['id'] != attempt][-8:]
    final['history'] = [event for event in final['history'] if event.get('plan') != key]
    final['attempt_outcomes'] = [outcome for outcome in final['attempt_outcomes'] if outcome['id'] != attempt]
    assert funding_history_issues(initial, records, final)
