"""Run a runtime Lua script against a mocked Factorio 1.1 or 2.0 API, built from
data-stage prototype tables. Test-only; needs `lupa`.

Like real LuaObjects, the mocked prototypes raise on members that the chosen
API version does not have, so version-specific reads must be guarded.
"""
from __future__ import annotations

import json

from lupa import LuaRuntime

STRICT = """
local function strict(values, label)
    return setmetatable(values, {__index = function(_, key)
        error(label .. " doesn't contain key " .. tostring(key), 2)
    end})
end
return strict
"""


def _products(recipe: dict) -> list[dict]:
    body = recipe.get("normal") if isinstance(recipe.get("normal"), dict) else recipe
    results = body.get("results")
    if results is None and body.get("result"):
        results = [{"name": body["result"], "amount": body.get("result_count", 1)}]
    products = []
    for entry in results or []:
        if isinstance(entry, list):
            entry = {"name": entry[0], "amount": entry[1] if len(entry) > 1 else 1}
        products.append({"type": entry.get("type", "item"), "name": entry["name"],
                         "amount": entry.get("amount", 1)})
    return products


def _enabled(recipe: dict) -> bool:
    body = recipe.get("normal") if isinstance(recipe.get("normal"), dict) else recipe
    return body.get("enabled", recipe.get("enabled", True)) is not False


def _ingredients(unit: dict, api: str) -> list[dict]:
    rows = []
    for entry in (unit or {}).get("ingredients") or []:
        name, amount = (entry[0], entry[1]) if isinstance(entry, list) else (entry["name"], entry["amount"])
        rows.append({"type": "item", "name": name, "amount": amount} if api == "1.1"
                    else {"name": name, "amount": amount})
    return rows


def run(script: str, data: dict, *, api: str, mods: dict, researched=(), current=None,
        progress=0.0, tick=0, agent_force: str | None = None) -> dict:
    """Execute `script` and return the JSON it printed through rcon.print."""
    assert api in ("1.1", "2.0")
    lua = LuaRuntime(unpack_returned_tuples=True)
    strict = lua.execute(STRICT)
    table = lambda value: lua.table_from(value) if isinstance(value, (dict, list)) else value

    def deep(value):
        if isinstance(value, dict):
            return lua.table_from({key: deep(item) for key, item in value.items()})
        if isinstance(value, list):
            return lua.table_from([deep(item) for item in value])
        return value

    technologies, recipes = {}, {}
    for name, recipe in (data.get("recipe") or {}).items():
        fields = {"name": name, "valid": True, "enabled": _enabled(recipe),
                  "products": deep(_products(recipe))}
        recipes[name] = strict(lua.table_from(fields), "LuaRecipePrototype")
    raw = data.get("technology") or {}
    for name, tech in raw.items():
        unit = tech.get("unit") or {}
        fields = {
            "name": name, "valid": True,
            "research_unit_ingredients": deep(_ingredients(unit, api)),
            "research_unit_count": unit.get("count", 0),
            "research_unit_energy": (unit.get("time") or 0) * 60,
            "effects": deep(tech.get("effects") or []),
            "hidden": bool(tech.get("hidden")), "enabled": tech.get("enabled", True) is not False,
            "upgrade": bool(tech.get("upgrade")), "order": tech.get("order", ""),
        }
        if unit.get("count_formula"):
            fields["research_unit_count_formula"] = unit["count_formula"]
        if tech.get("max_level") is not None:
            fields["max_level"] = 4294967295 if tech["max_level"] == "infinite" else tech["max_level"]
        if api == "2.0":
            fields["essential"] = bool(tech.get("essential"))
            if tech.get("research_trigger"):
                fields["research_trigger"] = deep(tech["research_trigger"])
        technologies[name] = strict(lua.table_from(fields), "LuaTechnologyPrototype")
    for name, tech in raw.items():
        prerequisites = {key: technologies[key] for key in tech.get("prerequisites") or [] if key in technologies}
        rawset = lua.eval("rawset")
        rawset(technologies[name], "prerequisites", lua.table_from(prerequisites))

    done = set(researched)
    force_techs = lua.table_from({name: lua.table_from({"name": name, "researched": name in done})
                                  for name in raw})
    force = {"name": agent_force or "player", "technologies": force_techs,
             "research_progress": progress}
    if current:
        force["current_research"] = technologies[current]
    force = strict(lua.table_from(force), "LuaForce")
    printed = []
    g = lua.globals()
    g.rcon = lua.table_from({"print": lambda text: printed.append(text)})
    g.script = lua.table_from({"active_mods": lua.table_from(mods)})
    encoder = lambda value: json.dumps(_plain(value))
    game = {"tick": tick, "forces": lua.table_from({"player": force})}
    agents = lua.table_from([lua.table_from({"valid": True, "force": force})]) if agent_force else None
    if api == "1.1":
        game.update(technology_prototypes=lua.table_from(technologies),
                    recipe_prototypes=lua.table_from(recipes), table_to_json=encoder)
        g["global"] = lua.table_from({"agent_characters": agents} if agents else {})
    else:
        g.prototypes = lua.table_from({"technology": lua.table_from(technologies),
                                       "recipe": lua.table_from(recipes)})
        g.helpers = lua.table_from({"table_to_json": encoder})
        g.storage = lua.table_from({"agent_characters": agents} if agents else {})
    g.game = strict(lua.table_from(game), "LuaGameScript")
    lua.execute(script)
    assert len(printed) == 1, printed
    return json.loads(printed[0])


def _plain(value, depth=0):
    if depth > 30:
        raise ValueError("too deep")
    if hasattr(value, "items"):
        keys = list(value.keys())
        if keys and all(isinstance(key, int) for key in keys):
            return [_plain(value[key], depth + 1) for key in sorted(keys)]
        # Factorio encodes an empty table as an empty JSON object.
        return {str(key): _plain(item, depth + 1) for key, item in value.items()}
    return value
