from __future__ import annotations

import copy
import dataclasses
import importlib.util
from pathlib import Path

import pytest

from jev_factorio.planning.catalog import Catalog
from jev_factorio.planning.materials import Recipe, requirements

_spec = importlib.util.spec_from_file_location(
    'catalog_fixture', Path(__file__).resolve().parents[1] / 'benchmarks/catalog_fixture.py')
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
OldCatalog, OldMaterials = _fixture.load_reference()


def outcome(call):
    try:
        return ('ok', dataclasses.asdict(call()))
    except (ValueError, KeyError, TypeError) as error:
        return ('error', type(error).__name__, str(error))


@pytest.mark.parametrize('seed', range(80))
def test_differential_material_decisions(seed):
    data, demand, inventory, researched = _fixture.fixture(12 + seed % 60, seed)
    # Include inaccessible research, partial stock and shared demands.
    if seed % 3 == 0:
        researched = researched[::2]
    old, new = OldCatalog.Catalog.from_dict(copy.deepcopy(data)), Catalog.from_dict(copy.deepcopy(data))
    original = copy.deepcopy((data, demand, inventory, researched))
    assert outcome(lambda: old.material_demands(demand, inventory, researched)) == outcome(
        lambda: new.material_demands(demand, inventory, researched))
    assert (data, demand, inventory, researched) == original


@pytest.mark.parametrize('case', [
    'ambiguous', 'named', 'disabled_preferred', 'hidden', 'probabilistic',
    'co_product', 'duplicate_product', 'barrel', 'oil_alternatives',
])
def test_alternative_selection_semantics(case):
    data, _, _, researched = _fixture.fixture(8)
    make = lambda name, products, **extra: dict(
        name=name, category='crafting', enabled=extra.pop('enabled', True),
        ingredients=[dict(name='ore', amount=1)], products=products, **extra)
    product = lambda name, **extra: dict(name=name, amount=1, **extra)
    data['recipes'] = {'first': make('first', [product('x')]), 'second': make('second', [product('x')])}
    if case == 'named':
        data['recipes']['x'] = make('x', [product('x')])
    elif case == 'disabled_preferred':
        data['recipes']['x'] = make('x', [product('x')], enabled=False)
    elif case == 'hidden':
        data['recipes']['second']['hidden'] = True
    elif case == 'probabilistic':
        data['recipes']['second']['products'][0]['probability'] = 0.5
    elif case == 'co_product':
        data['recipes'] = {'x': make('x', [product('x'), product('y')])}
    elif case == 'duplicate_product':
        data['recipes'] = {'x': make('x', [product('x'), product('x')])}
    elif case == 'barrel':
        data['recipes'] = {'x-barrel': make('x-barrel', [product('x')])}
    elif case == 'oil_alternatives':
        data['recipes'] = {
            'basic-oil-processing': make('basic-oil-processing', [product('petroleum-gas')]),
            'advanced-oil-processing': make('advanced-oil-processing',
                                          [product('petroleum-gas'), product('heavy-oil'), product('light-oil')]),
            'solid-fuel-from-petroleum-gas': make('solid-fuel-from-petroleum-gas', [product('solid-fuel')]),
            'other': make('other', [product('solid-fuel')]),
        }
    old, new = OldCatalog.Catalog.from_dict(copy.deepcopy(data)), Catalog.from_dict(copy.deepcopy(data))
    for item in ('x', 'y', 'petroleum-gas', 'heavy-oil', 'light-oil', 'solid-fuel'):
        def selected(catalog):
            try:
                return ('ok', catalog.recipe_for(item))
            except ValueError as error:
                return ('error', str(error))
        assert selected(old) == selected(new)
        assert outcome(lambda: old.material_plan(item, 7, {'ore': 4}, researched)) == outcome(
            lambda: new.material_plan(item, 7, {'ore': 4}, researched))


def test_indexes_read_only_and_not_retained():
    data, demand, inventory, researched = _fixture.fixture(12)
    catalog = Catalog.from_dict(data)
    products, unlocks = catalog._decision_indexes()
    with pytest.raises(TypeError):
        products['new'] = ()
    with pytest.raises(TypeError):
        unlocks['new'] = ()
    catalog.material_demands(demand, inventory, researched)
    assert not any('cache' in key or 'index' in key for key in vars(catalog))


@pytest.mark.parametrize('change', ['research', 'inventory', 'ingredient', 'product', 'enabled', 'technology'])
def test_rebuild_after_catalog_or_world_changes(change):
    data, demand, inventory, researched = _fixture.fixture(12)
    old, new = OldCatalog.Catalog.from_dict(copy.deepcopy(data)), Catalog.from_dict(copy.deepcopy(data))
    assert outcome(lambda: old.material_demands(demand, inventory, researched)) == outcome(
        lambda: new.material_demands(demand, inventory, researched))
    if change == 'research':
        researched.clear()
    elif change == 'inventory':
        inventory.update({'ore': 10000, 'item-11': 100})
    else:
        for catalog in (old, new):
            recipe = catalog.recipes['item-11']
            if change == 'ingredient':
                recipe['ingredients'][0]['amount'] = 20
            elif change == 'product':
                recipe['products'][0]['amount'] = 9
            elif change == 'enabled':
                recipe['enabled'] = False
                catalog.technologies['tech-11']['effects'].clear()
            elif change == 'technology':
                catalog.technologies['tech-11']['effects'].clear()
    assert outcome(lambda: old.material_demands(demand, inventory, researched)) == outcome(
        lambda: new.material_demands(demand, inventory, researched))


@pytest.mark.parametrize('case', ['cycle', 'ambiguous', 'selected_disabled', 'reservation', 'budget', 'duplicate', 'coproduct'])
def test_material_solver_guards_and_receipts_unchanged(case):
    recipes = [Recipe('a', {'ore': 2}, {'a': 2}), Recipe('b', {'a': 1}, {'b': 1})]
    selected, reserved, limit = {}, {}, 1000
    if case == 'cycle':
        recipes[0] = Recipe('a', {'b': 1}, {'a': 1})
    elif case == 'ambiguous':
        recipes.append(Recipe('another', {'ore': 1}, {'b': 1}))
    elif case == 'selected_disabled':
        recipes.append(Recipe('disabled', {'ore': 1}, {'b': 1}, enabled=False))
        selected = {'b': 'disabled'}
    elif case == 'reservation':
        reserved = {'ore': 2}
    elif case == 'budget':
        limit = 1
    elif case == 'duplicate':
        recipes.append(recipes[0])
    elif case == 'coproduct':
        recipes = [Recipe('ab', {'ore': 1}, {'a': 2, 'b': 3})]
    old = [OldMaterials.Recipe(**dataclasses.asdict(row)) for row in recipes]
    args = dict(selected=selected, reserved=reserved, max_expansions=limit)
    assert outcome(lambda: OldMaterials.requirements({'a': 1, 'b': 7}, {'ore': 1}, old, **args)) == outcome(
        lambda: requirements({'a': 1, 'b': 7}, {'ore': 1}, recipes, **args))


def test_duplicate_unlock_effects_and_sorted_technology_order():
    data, _, _, _ = _fixture.fixture(8)
    data['technologies'] = {'z': {'effects': [{'type': 'unlock-recipe', 'recipe': 'item-1'}] * 2},
                            'a': {'effects': [{'type': 'unlock-recipe', 'recipe': 'item-1'}]}}
    c = Catalog.from_dict(data)
    assert c.unlocks('item-1') == ['a', 'z']
    assert c._decision_indexes()[1]['item-1'] == ('a', 'z')


@pytest.mark.parametrize('recipe_value', [[], {}, None, 7])
def test_non_string_unlock_effect_preserves_legacy_no_match(recipe_value):
    data, demand, inventory, researched = _fixture.fixture(12)
    data['technologies']['malformed'] = {
        'effects': [{'type': 'unlock-recipe', 'recipe': recipe_value}]}
    researched.append('malformed')
    old = OldCatalog.Catalog.from_dict(copy.deepcopy(data))
    new = Catalog.from_dict(copy.deepcopy(data))
    assert outcome(lambda: old.material_demands(demand, inventory, researched)) == outcome(
        lambda: new.material_demands(demand, inventory, researched))
