from copy import deepcopy
import pytest
from jev_factorio import coal_supply as c, solid_routes as s
from jev_factorio.factory_contract import allowed, satisfied, validate_command
from jev_factorio.planning.coal_supply import candidates, source_project
from coal_supply_fixtures import fixture, full, paid_source


def test_native_double_wire_roundtrips_strict_python_contract():
    before=fixture();rows=c.sources(before)
    assert set(rows)=={'alpha','beta'}
    assert all(c.current(r,before) for r in rows.values())
    after=full();assert len(s.routes(after))==2 and c.flow_complete(after)
    assert not c.flow_complete(before)


def test_balanced_construction_preserves_failure_project_and_full_kit():
    state=fixture();plans=candidates(state,'iron_smelting');assert len(plans)==2
    p=plans[0];assert p.steps[0].allowed(state) and not p.steps[0].satisfied(state)
    first=p.steps[0].parameters['target'];paid_source(state,p.steps[0].parameters)
    assert p.steps[0].satisfied(state)
    assert candidates(state,'iron_smelting')[0].steps[0].parameters['target'] != first
    assert source_project('alpha','chest')==plans[0].id
    assert not candidates(fixture(),'iron_smelting',failures={p.id:2 for p in plans})


@pytest.mark.parametrize('mutation',[
    lambda x:x.factory['coal_supply'].update(tick=x.tick-1),
    lambda x:x.factory['coal_supply']['sources'].pop('beta'),
    lambda x:x.factory['coal_supply']['sources']['alpha']['power'].update(witness_unit=True),
    lambda x:x.factory['coal_supply']['sources']['alpha'].update(layout='different'),
    lambda x:x.factory['coal_supply']['sources']['alpha']['target'].update(unit_number=True),
    lambda x:x.factory['coal_supply'].update(actor_index=2),
])
def test_malformed_bundle_denies_fresh_construction(mutation):
    state=fixture();p=candidates(state,'iron_smelting')[0].steps[0];mutation(state)
    assert not p.allowed(state)
    with pytest.raises(ValueError): c.sources(state)


def test_unmatched_or_unqualified_power_is_not_permission():
    state=fixture();p=candidates(state,'iron_smelting')[0].steps[0]
    power=c.sources(state)['alpha']['power'];state.factory['entities'][power['witness_role']]['energy']=0
    assert not p.allowed(state)
    state=fixture();state.inventory['electric-mining-drill']=1
    assert not p.allowed(state) and not candidates(state,'iron_smelting')


def test_flow_requires_whole_bundle_and_exact_matching_solid_telemetry():
    state=full();rows=c.sources(state);assert c.flow_complete(state)
    rows['beta']['flow']['positive_samples']=2
    assert not c.flow_complete(state)
    state=full();next(iter(state.factory['solid_routes']['routes'].values()))['flow']['received']+=1
    with pytest.raises(ValueError): s.routes(state)
    assert not c.flow_complete(state)


def test_manual_insert_allowed_source_extraction_denied():
    state=full()
    params={'role':'alpha','item':'coal','quantity':2,'receipt':'manual:contract'}
    assert c.manual_permitted('factory_insert',params,state)
    assert s.permits('factory_insert',params,state)
    assert not c.permits('factory_extract',params,state)
    assert not c.permits('factory_extract',{**params,'role':'coal:alpha:chest'},state)


def test_commitment_retains_paid_prefix_and_not_live_permission():
    state=fixture();row=c.sources(state)['alpha'];saved=c.commitment(row);c.validate_commitment(saved,'alpha')
    assert 'flow' not in saved and 'power' not in saved
    assert not c.reconciles(saved,row)
    paid_source(state,candidates(state,'iron_smelting')[0].steps[0].parameters)
    assert c.reconciles(saved,c.sources(state)['alpha'])
