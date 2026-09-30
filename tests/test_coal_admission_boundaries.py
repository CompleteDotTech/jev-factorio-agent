"""Economic deferral covers carried construction and payment boundaries.

These are composed-controller doubles, not positive native economic evidence.
"""
from copy import deepcopy
from dataclasses import asdict

import pytest

from jev_factorio import coal_supply
from jev_factorio.planning import coal_admission, coal_funding
from jev_factorio.planning.coal_supply import candidates
from test_coal_admission_contract import v2
from test_coal_kit_funding import Backend, controller, offers


def carried_backend(version):
    backend = Backend()
    if version == 2:
        backend.state = v2()
    backend.state.inventory.update(
        coal_supply.remaining_kit(coal_supply.sources(backend.state), backend.state))
    return backend


@pytest.mark.parametrize("version", [1, 2])
def test_unqualified_carried_kit_never_starts_or_pays(version, tmp_path):
    backend = carried_backend(version)
    stock = deepcopy(backend.state.inventory)
    loop = controller(backend, tmp_path, coal_economic_admission=True)
    _, plans = offers(loop)
    assert not plans
    assert loop._coal_admission_evidence["eligible"] is False
    loop.step()
    assert not backend.calls
    assert backend.state.inventory == stock
    assert not loop.memory.coal_commitments
    assert loop.memory.coal_funding is None
    assert loop.memory.pending is None


@pytest.mark.parametrize("version", [1, 2])
def test_unqualified_direct_build_fails_commit_and_dispatch_boundaries(version, tmp_path):
    backend = carried_backend(version)
    loop = controller(backend, tmp_path, coal_economic_admission=True)
    snapshot = loop._observe()
    # The raw geometry planner is deliberately independent of controller policy.
    plan = candidates(snapshot, loop.memory.active_goal)[0]
    before = asdict(loop.memory)
    assert not loop._step_allowed(plan.steps[0], snapshot)
    assert not loop._investment_step_allowed(plan, plan.steps[0], snapshot)
    with pytest.raises(ValueError, match="economic admission"):
        loop._commit_solid(plan, snapshot)
    assert asdict(loop.memory) == before
    assert not backend.calls


@pytest.mark.parametrize("version", [1, 2])
def test_unoffered_kit_cannot_create_funding_at_commit(version, tmp_path):
    backend = carried_backend(version)
    backend.state.inventory['electric-mining-drill'] = 0
    backend.state.factory['entities']['kit:storage'] = {
        'unit_number': 6000, 'name': 'wooden-chest', 'position': {'x': 2, 'y': 2},
        'output': {'electric-mining-drill': 2}}
    loop = controller(backend, tmp_path, coal_economic_admission=True)
    snapshot = loop._observe()
    plan, _ = coal_funding.candidate(snapshot, loop.catalog, **loop._coal_funding_options())
    assert plan is not None
    before = asdict(loop.memory)
    with pytest.raises(ValueError, match="economic admission"):
        loop._commit_solid(plan, snapshot)
    assert asdict(loop.memory) == before
    assert loop.memory.coal_funding is None and not backend.calls


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_carried_policy_still_builds(version, tmp_path):
    backend = carried_backend(version)
    loop = controller(backend, tmp_path)
    record = loop.step()
    assert record['action'] == coal_supply.COMMAND and record['verified']
    assert len(backend.calls) == 1


def test_changed_economic_observation_rechecked_after_selection(monkeypatch, tmp_path):
    backend = carried_backend(2)
    loop = controller(backend, tmp_path, coal_economic_admission=True)
    # Controlled policy input exercises a formerly selected plan. It does not
    # change the native protocol, whose positive economic path remains absent.
    with monkeypatch.context() as patch:
        patch.setattr(coal_admission, 'evaluate', lambda *args: {'eligible': True})
        snapshot, plans = offers(loop)
        plan = next(plan for plan in plans if plan.steps[0].action == coal_supply.COMMAND)
        loop._commit_solid(plan, snapshot)
    fresh = loop._observe()
    assert not loop._step_allowed(plan.steps[0], fresh)
    assert not loop._investment_step_allowed(plan, plan.steps[0], fresh)
    assert loop._coal_admission_evidence['eligible'] is False
    assert not backend.calls and not loop.memory.coal_commitments


@pytest.mark.parametrize('recovery', ['paid', 'prepared', 'lost_ack'])
def test_retained_network_continues_after_economics_defers(recovery, monkeypatch, tmp_path):
    backend = carried_backend(2)
    backend.prepared_once = recovery == 'prepared'
    backend.lost_ack = recovery == 'lost_ack'
    loop = controller(backend, tmp_path, coal_economic_admission=True)
    # Seed owned work through the real paid/prepared controller path. This
    # synthetic positive input is scoped to setup and is never native evidence.
    with monkeypatch.context() as patch:
        patch.setattr(coal_admission, 'evaluate', lambda *args: {'eligible': True})
        first = loop.step()
    assert first['verified'] is (recovery == 'paid')
    assert len(backend.calls) == 1
    assert coal_admission.evaluate(backend.state)['eligible'] is False
    pending = deepcopy(loop.memory.pending)
    failures = deepcopy(loop.memory.failures)
    backend.lost_ack = False
    resumed = controller(backend, tmp_path, resume=True, coal_economic_admission=True)
    result = resumed.step()
    assert result['verified'], result
    assert len(backend.calls) == (1 if recovery == 'lost_ack' else 2)
    if recovery == 'prepared':
        assert backend.calls[0] == backend.calls[1]
        assert pending is not None
    assert resumed.memory.failures == failures
    assert resumed.memory.pending is None
    assert resumed.memory.coal_commitments


def test_paid_funding_continues_when_new_admission_defers(monkeypatch, tmp_path):
    backend = Backend()
    loop = controller(backend, tmp_path, coal_economic_admission=True)
    with monkeypatch.context() as patch:
        patch.setattr(coal_admission, 'evaluate', lambda *args: {'eligible': True})
        assert loop.step()['verified']
    assert len(backend.calls) == 1 and loop.memory.coal_funding is not None
    assert coal_admission.evaluate(backend.state)['eligible'] is False
    resumed = controller(backend, tmp_path, resume=True, coal_economic_admission=True)
    record = resumed.step()
    assert record['verified'] and record['action'] == coal_supply.COMMAND
    assert len(backend.calls) == 2
    assert resumed.memory.coal_funding is None and resumed.memory.coal_commitments
