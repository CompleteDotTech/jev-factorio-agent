"""Bounded, deterministic material expansion; never ask a model to do arithmetic.

This is an inventory/batch calculator, not a throughput or fluid-network solver.
Recipes must come from a versioned backend export. Alternative recipes require
an explicit selection, and cyclic production requires a different solver.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def quantities(values: dict[str, float]) -> dict[str, float]:
    result = {}
    for name, value in values.items():
        if not isinstance(name, str) or not name or isinstance(value, bool):
            raise ValueError("Invalid material entry")
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid quantity for {name}")
        result[name] = float(value)
    return result


@dataclass(frozen=True)
class Recipe:
    id: str
    ingredients: dict[str, float]
    products: dict[str, float]
    enabled: bool = True

    def __post_init__(self) -> None:
        quantities(self.ingredients)
        quantities(self.products)
        if not self.id or not self.products or not all(v > 0 for v in self.products.values()):
            raise ValueError("A recipe needs positive outputs")


@dataclass
class MaterialPlan:
    batches: dict[str, int] = field(default_factory=dict)
    shortages: dict[str, float] = field(default_factory=dict)
    remaining: dict[str, float] = field(default_factory=dict)


def requirements(demand: dict[str, float], inventory: dict[str, float],
                 recipes: list[Recipe], selected: dict[str, str] | None = None,
                 reserved: dict[str, float] | None = None,
                 max_expansions: int = 1000) -> MaterialPlan:
    """Expand demand once per consumed unit, accounting for batch co-products."""
    demand, stock = quantities(demand), quantities(inventory)
    for item, amount in quantities(reserved or {}).items():
        if amount > stock.get(item, 0):
            raise ValueError(f"Reservation exceeds inventory: {item}")
        stock[item] = stock.get(item, 0) - amount
    if len({r.id for r in recipes}) != len(recipes):
        raise ValueError("Duplicate recipe IDs")
    selected = selected or {}
    result = MaterialPlan()
    expansions = 0

    def consume(item: str, amount: float, path: tuple[str, ...]) -> None:
        nonlocal expansions
        available = min(stock.get(item, 0), amount)
        stock[item] = stock.get(item, 0) - available
        amount -= available
        if amount <= 1e-9:
            return
        expansions += 1
        if expansions > max_expansions:
            raise ValueError("Material expansion budget exceeded")
        if item in path:
            raise ValueError(f"Cyclic production dependency: {' -> '.join((*path, item))}")
        choices = [r for r in recipes if r.enabled and item in r.products]
        if item in selected:
            choices = [r for r in choices if r.id == selected[item]]
            if not choices:
                raise ValueError(f"Selected recipe is unavailable for {item}")
        if len(choices) > 1:
            raise ValueError(f"Select an alternative recipe explicitly for {item}")
        if not choices:
            result.shortages[item] = result.shortages.get(item, 0) + amount
            return
        recipe = choices[0]
        count = math.ceil(amount / recipe.products[item])
        for ingredient, required in sorted(recipe.ingredients.items()):
            consume(ingredient, required * count, (*path, item))
        result.batches[recipe.id] = result.batches.get(recipe.id, 0) + count
        for product, produced in recipe.products.items():
            stock[product] = stock.get(product, 0) + produced * count
        stock[item] -= amount

    for item, amount in sorted(demand.items()):
        consume(item, amount, ())
    result.remaining = stock
    return result
