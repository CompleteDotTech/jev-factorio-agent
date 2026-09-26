"""Deterministic synthetic catalogs; not captured production measurements."""
from __future__ import annotations

import importlib.util
import random
import sys
import types
from pathlib import Path


def load_reference():
    root = Path(__file__).resolve().parents[1] / 'tests/fixtures/performance_reference'
    prefix = '_jev_material_index_reference'
    if prefix + '.catalog' in sys.modules:
        return sys.modules[prefix + '.catalog'], sys.modules[prefix + '.materials']
    package = types.ModuleType(prefix)
    package.__path__ = [str(root)]
    sys.modules[prefix] = package
    for name in ('materials', 'catalog'):
        spec = importlib.util.spec_from_file_location(prefix + '.' + name, root / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules[prefix + '.catalog'], sys.modules[prefix + '.materials']


def fixture(size: int, seed: int = 0):
    if type(size) is not int or not 8 <= size <= 10000:
        raise ValueError('Fixture size must be 8..10000')
    rng = random.Random(seed)
    recipes, technologies = {}, {}
    for index in range(size):
        name = f'item-{index}'
        # Acyclic chains and shared raw stock. Depth stays bounded by 12.
        parent = f'item-{index - 1}' if index % 12 else 'ore'
        recipes[name] = {
            'name': name, 'category': 'crafting', 'enabled': index % 3 == 0,
            'hidden': False, 'energy': 0.5,
            'ingredients': [{'type': 'item', 'name': parent, 'amount': rng.randint(1, 3)}],
            'products': [{'type': 'item', 'name': name, 'amount': rng.randint(1, 3)}],
        }
        technologies[f'tech-{index}'] = {
            'effects': [{'type': 'unlock-recipe', 'recipe': name}],
            'enabled': True, 'researched': False,
        }
    data = {'version': '2.0.72', 'mods': {'base': '2.0.72'},
            'recipes': recipes, 'technologies': technologies,
            'machines': {}, 'hand_categories': {'crafting': True}, 'stack_sizes': {'coal': 50}}
    demand = {f'item-{i}': rng.randint(1, 20) for i in range(size - 8, size)}
    inventory = {'ore': rng.randint(0, 100), f'item-{size - 3}': 2}
    researched = list(technologies)
    return data, demand, inventory, researched
