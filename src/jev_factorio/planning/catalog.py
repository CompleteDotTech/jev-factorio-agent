"""Version-bound native recipe and technology facts, separate from model advice."""
from __future__ import annotations

from dataclasses import dataclass, field

from .materials import MaterialPlan, Recipe, quantities, requirements


@dataclass(frozen=True)
class Catalog:
    version: str
    recipes: dict
    technologies: dict
    machines: dict
    hand_categories: dict
    stack_sizes: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Catalog:
        if not str(data.get("version", "")).startswith("2.0."):
            raise ValueError("Native campaign currently requires Factorio 2.0")
        if set(data.get("mods", {})) - {"base", "core"}:
            raise ValueError("Native campaign currently supports unmodified base-game recipes")
        for key in ("recipes", "technologies", "machines", "hand_categories"):
            if not isinstance(data.get(key), dict):
                raise ValueError(f"Invalid native catalog field: {key}")
        if len(data["recipes"]) > 10000 or len(data["technologies"]) > 10000:
            raise ValueError("Native catalog exceeds the supported budget")
        for name, recipe in data["recipes"].items():
            if recipe["name"] != name or not isinstance(recipe["category"], str):
                raise ValueError("Recipe identity mismatch")
            quantities({entry["name"]: entry["amount"] for entry in recipe["ingredients"]})
        stack_sizes = data.get("stack_sizes", {})
        if not isinstance(stack_sizes, dict) or any(
            type(size) is not int or size < 1 for size in stack_sizes.values()
        ):
            raise ValueError("Invalid native item stack sizes")
        return cls(data["version"], data["recipes"], data["technologies"],
                   data["machines"], data["hand_categories"], stack_sizes)

    def unlocks(self, recipe: str) -> list[str]:
        return sorted(
            name for name, tech in self.technologies.items()
            if any(effect.get("type") == "unlock-recipe" and effect.get("recipe") == recipe
                   for effect in tech["effects"])
        )

    def enabled(self, recipe: dict, researched: list[str]) -> bool:
        return recipe["enabled"] or bool(set(self.unlocks(recipe["name"])) & set(researched))

    def recipe_for(self, item: str) -> dict:
        alternatives = {
            "petroleum-gas": "basic-oil-processing",
            "heavy-oil": "advanced-oil-processing",
            "light-oil": "advanced-oil-processing",
            "solid-fuel": "solid-fuel-from-petroleum-gas",
        }
        preferred = alternatives.get(item, item)
        choices = [
            recipe for recipe in self.recipes.values() if not recipe.get("hidden")
            and any(product["name"] == item and product.get("probability", 1) == 1
                    and product.get("amount", 0) > 0 for product in recipe["products"])
        ]
        named = [recipe for recipe in choices if recipe["name"] == preferred]
        if named:
            return named[0]
        if len(choices) != 1:
            raise ValueError(f"No unambiguous deterministic native recipe for {item}")
        return choices[0]

    def material_plan(self, item: str, amount: int, inventory: dict,
                      researched: list[str]) -> MaterialPlan:
        return self.material_demands({item: amount}, inventory, researched)

    def material_demands(self, demand: dict[str, int], inventory: dict,
                         researched: list[str]) -> MaterialPlan:
        """Expand several tasks against one shared stock ledger."""
        recipes, selected = [], {}
        for recipe in self.recipes.values():
            if (not self.enabled(recipe, researched) or recipe.get("hidden")
                    or not recipe["products"]
                    or "barrel" in recipe["name"]
                    or any(product.get("probability", 1) != 1
                           or product.get("amount", 0) <= 0 for product in recipe["products"])):
                continue
            recipes.append(Recipe(
                recipe["name"],
                {entry["name"]: entry["amount"] for entry in recipe["ingredients"]},
                {entry["name"]: entry["amount"] for entry in recipe["products"]},
            ))
            for product in recipe["products"]:
                try:
                    selected[product["name"]] = self.recipe_for(product["name"])["name"]
                except ValueError:
                    pass
        return requirements(demand, inventory, recipes, selected=selected)
