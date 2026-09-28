"""Ordinary composed selection, not a stubbed science frontier or native game."""
from copy import deepcopy
from dataclasses import replace
import json

from jev_factorio.coal_controller import coal_loop_type
from jev_factorio.solid_controller import solid_loop_type
from jev_factorio.controller import HierarchicalLoop
from jev_factorio.planning import coal_funding
from jev_factorio.planning.decision_support import scheduling_context
from jev_factorio.jev_client import MockJevClient

from test_coal_kit_funding import Backend as KitBackend, controller, offers
from test_factory import machine


class Backend(KitBackend):
    def execute(self, action, p):
        if action in {'factory_extract', 'factory_insert'} and p.get('item') == 'automation-science-pack':
            saved = json.loads(self.checkpoint.read_text())
            assert saved['pending']['dispatch'] == 'prepared'
            assert saved['active_plan']['steps'][0]['parameters'] == p
            self.calls.append((action, deepcopy(p)))
            if action == 'factory_extract':
                self.state.factory['entities'][p['role']]['output'][p['item']] -= p['quantity']
                self.state.inventory[p['item']] = self.state.inventory.get(p['item'], 0) + p['quantity']
            else:
                self.state.inventory[p['item']] -= p['quantity']
                self.state.factory['entities'][p['role']]['input'][p['item']] = p['quantity']
            self.state.factory['receipts'][p['receipt']] = {
                'role': p['role'], 'item': p['item'], 'quantity': p['quantity'],
                'extracting': action == 'factory_extract',
                'unit_number': self.state.factory['entities'][p['role']]['unit_number']}
            self.advance()
            return 'verified synthetic science transfer'
        return super().execute(action, p)


class KitSeekingModel(MockJevClient):
    is_mock = False

    def __init__(self):
        self.offered = []

    def evaluate(self, state, questions):
        answers = super().evaluate(state, questions)
        choices = list(questions['candidate']['criteria'])
        self.offered.append(choices)
        preferred = next((key for key in choices if key.startswith('coal-kit:')), choices[0])
        answers['candidate']['choice'] = preferred
        answers['candidate']['probabilities'] = {key: float(key == preferred) for key in choices}
        return answers


def scenario(tmp_path):
    backend = Backend()
    backend.state.factory.update(research='study', research_progress=0)
    entities = backend.state.factory['entities']
    entities['utility:lab'] = dict(name='lab', unit_number=8001, energy=100,
                                   input={}, position={'x': 9, 'y': 9}, electric_network_id=10)
    entities['recipe:automation-science-pack'] = dict(
        name='assembling-machine-1', unit_number=8000, energy=100,
        recipe='automation-science-pack', input={}, position={'x': 100, 'y': 100},
        output={'automation-science-pack': 1})
    entities.update({
        'utility:water': machine('offshore-pump', unit_number=8101,
                                 fluid_ports=[{'id': 1, 'fluid': 'water'}]),
        'utility:boiler': machine('boiler', unit_number=8102, fuel={'coal': 10},
                                  fluid_ports=[{'id': 1, 'fluid': 'water'},
                                               {'id': 2, 'fluid': 'steam'}]),
        'utility:engine': machine('steam-engine', unit_number=8103, electric_network_id=10,
                                  fluid_ports=[{'id': 2, 'fluid': 'steam'}]),
    })
    loop = controller(backend, tmp_path,
                      kind=coal_loop_type(solid_loop_type(HierarchicalLoop)))
    return loop, backend


def test_ready_science_selected_and_executed_before_optional_kit(tmp_path):
    loop, backend = scenario(tmp_path)
    snapshot, plans = offers(loop)
    context = scheduling_context(snapshot, loop.catalog, plans, 'rocket_launch')
    science = next(p for p in plans if p.steps[0].parameters.get('item') == 'automation-science-pack')
    assert context['deterministic_ranking'][0] == science.id
    assert not any(coal_funding.MARKER in (p.materials or {}) for p in plans)
    assert loop._coal_kit_evidence['selection_deferred_reason'] == 'ready_required_science'
    record = loop.step()
    assert record['verified'] and backend.calls[0][1]['item'] == 'automation-science-pack'
    assert backend.state.factory['entities']['recipe:automation-science-pack']['output']['automation-science-pack'] == 0
    # A background assembler can finish another pack before the next decision.
    # Each new observation must protect actually ready required science.
    backend.state.factory['entities']['recipe:automation-science-pack']['output']['automation-science-pack'] = 1
    second = loop.step()
    assert second['verified'] and backend.calls[1][1]['item'] == 'automation-science-pack'
    assert backend.state.inventory['automation-science-pack'] == 2


def test_only_canonical_current_kit_gets_optional_priority(tmp_path):
    loop, backend = scenario(tmp_path)
    backend.state.factory['entities']['recipe:automation-science-pack']['output']['automation-science-pack'] = 0
    snapshot, plans = offers(loop)
    kit = next(p for p in plans if coal_funding.MARKER in (p.materials or {}))
    assert coal_funding.ranking_marker(kit, snapshot)
    forged = deepcopy(kit)
    forged.materials['coal_kit_cost']['acquisition_actions_estimate'] += 1
    assert not coal_funding.ranking_marker(forged, snapshot)
    changed = deepcopy(snapshot)
    changed.tick += 1
    assert not coal_funding.ranking_marker(kit, changed)
    unrelated = replace(kit, id='forged-coal-project')
    assert not coal_funding.ranking_marker(unrelated, snapshot)


def test_healthy_model_cannot_select_optional_kit_while_science_is_ready(tmp_path):
    loop, backend = scenario(tmp_path)
    model = KitSeekingModel()
    loop.policy = 'jev'
    loop.jev = model
    for _ in range(2):
        record = loop.step()
        assert record['verified'] and backend.calls[-1][1]['item'] == 'automation-science-pack'
        assert model.offered and not any(key.startswith('coal-kit:') for key in model.offered[-1])
        backend.state.factory['entities']['recipe:automation-science-pack']['output']['automation-science-pack'] = 1
    backend.state.factory['entities']['recipe:automation-science-pack']['output']['automation-science-pack'] = 0
    _, plans = offers(loop)
    assert any(coal_funding.MARKER in (p.materials or {}) for p in plans)


def test_committed_kit_continuation_is_not_silently_removed(tmp_path):
    loop, _ = scenario(tmp_path)
    snapshot = loop._observe()
    kit, _ = coal_funding.candidate(snapshot, loop.catalog, **loop._coal_funding_options())
    assert kit is not None
    loop.memory.coal_funding = coal_funding.start(kit, snapshot, loop.catalog)
    _, plans = offers(loop)
    assert any(coal_funding.MARKER in (p.materials or {}) for p in plans)
