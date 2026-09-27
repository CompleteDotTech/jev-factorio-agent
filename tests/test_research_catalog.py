"""Version-aware research tree: Lua export on 1.1/2.0 APIs, validation, analysis, dashboard."""
import json
import os
from pathlib import Path

import pytest

from jev_factorio import research_catalog as rc
from jev_factorio.dashboard import EventWriter, Monitor, project_record, project_state

SCRIPT = rc.lua_source()


def tech(prerequisites=(), packs=(), **extra):
    unit = {"count": 10, "time": 5, "ingredients": [[pack, 1] for pack in packs]}
    return {"prerequisites": list(prerequisites), "unit": unit, **extra}


def unlock(*recipes):
    return [{"type": "unlock-recipe", "recipe": recipe} for recipe in recipes]


def recipe(product, enabled=False):
    return {"results": [{"type": "item", "name": product, "amount": 1}], "enabled": enabled}


# A miniature 2.0-style tree: a trigger tech unlocks red science; the silo needs blue.
DATA_20 = {
    "technology": {
        "electronics": tech(research_trigger={"type": "craft-item", "item": "copper-cable", "count": 10}),
        "automation-science-pack": tech(["electronics"], effects=unlock("automation-science-pack"), essential=True,
                                        research_trigger={"type": "craft-item", "item": "iron-gear-wheel", "count": 10}),
        "automation": tech(["automation-science-pack"], ["automation-science-pack"]),
        "logistic-science-pack": tech(["automation"], ["automation-science-pack"],
                                      effects=unlock("logistic-science-pack"), essential=True),
        "military": tech(["automation-science-pack"], ["automation-science-pack"]),
        "military-science-pack": tech(["military", "logistic-science-pack"],
                                      ["automation-science-pack", "logistic-science-pack"],
                                      effects=unlock("military-science-pack"), essential=True),
        "oil-processing": tech(["logistic-science-pack"], ["automation-science-pack", "logistic-science-pack"]),
        "chemical-science-pack": tech(["oil-processing"], ["automation-science-pack", "logistic-science-pack"],
                                      effects=unlock("chemical-science-pack"), essential=True),
        "rocket-silo": tech(["chemical-science-pack"], ["automation-science-pack", "logistic-science-pack",
                                                        "chemical-science-pack"],
                            effects=unlock("rocket-silo"), essential=True),
        "mining-productivity-3": tech(["rocket-silo"], ["chemical-science-pack"],
                                      max_level="infinite", unit={"count_formula": "1000*L", "time": 60,
                                                                  "ingredients": [["chemical-science-pack", 1]]}),
        "debug-hidden": tech(hidden=True),
    },
    "recipe": {
        "automation-science-pack": recipe("automation-science-pack"),
        "logistic-science-pack": recipe("logistic-science-pack"),
        "military-science-pack": recipe("military-science-pack"),
        "chemical-science-pack": recipe("chemical-science-pack"),
        "rocket-silo": recipe("rocket-silo"),
        "iron-gear-wheel": recipe("iron-gear-wheel", enabled=True),
    },
}

# The same shape on 1.1: red science is available from the start, no triggers.
DATA_11 = {
    "technology": {name: dict(value, research_trigger=None, essential=None)
                   for name, value in DATA_20["technology"].items() if name not in ("electronics", "automation-science-pack")},
    "recipe": dict(DATA_20["recipe"], **{"automation-science-pack": recipe("automation-science-pack", enabled=True)}),
}
DATA_11["technology"]["automation"] = tech([], ["automation-science-pack"])


def export_with(data, api, mods, **state):
    mock = pytest.importorskip("factorio_runtime_mock")
    pytest.importorskip("lupa")
    return rc.parse(mock.run(SCRIPT, data, api=api, mods=mods, **state))


def test_lua_export_on_20_api_reads_triggers_essentials_and_agent_force():
    catalog = export_with(DATA_20, "2.0", {"base": "2.0.77"}, researched=["electronics", "automation-science-pack"],
                          current="automation", progress=0.5, tick=900, agent_force="player")
    assert catalog["version"] == "2.0.77"
    assert catalog["technologies"]["automation-science-pack"]["trigger"] == {
        "type": "craft-item", "item": "iron-gear-wheel", "count": 10}
    assert catalog["technologies"]["rocket-silo"]["essential"] is True
    assert catalog["technologies"]["mining-productivity-3"]["infinite"] is True
    assert catalog["science_packs"]["automation-science-pack"] == {
        "from_start": False, "unlocked_by": ["automation-science-pack"]}
    state = catalog["state"]
    assert set(state["researched"]) == {"electronics", "automation-science-pack"}
    assert (state["tick"], state["current"], state["progress"]) == (900, "automation", 0.5)


def test_lua_export_on_11_api_guards_members_that_do_not_exist():
    # The 1.1 mock raises on research_trigger/essential, like real LuaObjects.
    catalog = export_with(DATA_11, "1.1", {"base": "1.1.110"}, researched=["automation"])
    assert catalog["version"] == "1.1.110"
    assert all(t["trigger"] is None and t["essential"] is False for t in catalog["technologies"].values())
    assert catalog["science_packs"]["automation-science-pack"]["from_start"] is True
    assert catalog["state"]["current"] is None


def catalog(data, mods=None, researched=None):
    """A parsed catalog built directly from data-stage fixtures (no Lua)."""
    return rc.parse(raw_catalog(data, mods, researched))


def raw_catalog(data, mods=None, researched=None):
    """The exporter's raw output format (what the sidecar stores)."""
    techs = {}
    for name, value in data["technology"].items():
        unit = value.get("unit") or {}
        techs[name] = {
            "prerequisites": value.get("prerequisites", []),
            "ingredients": [{"name": p, "amount": a} for p, a in unit.get("ingredients", [])],
            "count": unit.get("count"), "count_formula": unit.get("count_formula"),
            "trigger": value.get("research_trigger"), "hidden": bool(value.get("hidden")),
            "essential": bool(value.get("essential")),
            "unlocks": [e["recipe"] for e in value.get("effects", []) if e["type"] == "unlock-recipe"],
            "locations": [e["space_location"] for e in value.get("effects", []) if e["type"] == "unlock-space-location"],
        }
    packs = {}
    for name, value in techs.items():
        for pack in value["ingredients"]:
            packs.setdefault(pack["name"], {"from_start": False, "unlocked_by": []})
    for name, value in techs.items():
        for made in value["unlocks"]:
            if made in packs:
                packs[made]["unlocked_by"].append(name)
    for name, value in data["recipe"].items():
        product = value["results"][0]["name"]
        if product in packs and value.get("enabled"):
            packs[product]["from_start"] = True
    raw = {"schema": rc.SCHEMA, "version": "2.0.77", "mods": mods or {"base": "2.0.77"},
           "technologies": techs, "science_packs": packs}
    if researched is not None:
        raw["state"] = {"tick": 5, "researched": researched, "current": None, "progress": None}
    return raw


def test_base_tree_runs_to_the_rocket_silo_with_game_ordered_tiers():
    tree = rc.Tree(catalog(DATA_20))
    assert tree.goals == ["rocket-silo"]
    order = [p for p in tree.packs if p != "military-science-pack"]
    assert order == ["automation-science-pack", "logistic-science-pack", "chemical-science-pack"]
    # Military is essential in the game, but not needed for the silo: not on the path.
    assert "military" not in tree.path and "military-science-pack" not in tree.path
    assert "mining-productivity-3" not in tree.path and "debug-hidden" not in tree.path
    summary = tree.summary({"electronics", "automation-science-pack", "automation"}, "logistic-science-pack", 0.25, {})
    assert summary["goal"] == "Rocket silo"
    assert [m["title"] for m in summary["milestones"]] == [
        "Automation science", "Logistic science", "Chemical science", "Rocket silo"]
    assert [m["state"] for m in summary["milestones"]] == ["done", "pending", "pending", "pending"]
    tiers = {t["pack"]: (t["done"], t["total"]) for t in summary["tiers"]}
    assert tiers["automation-science-pack"] == (3, 4)  # electronics, red-science trigger, automation / + logistic
    assert summary["current"]["pack"] == "automation-science-pack" and summary["current"]["progress"] == 0.25
    assert summary["available"] == 1 and summary["path_done"] == 3


def test_11_tree_marks_the_starting_pack_as_available_from_start():
    tree = rc.Tree(catalog(DATA_11))
    summary = tree.summary(set(), None, None, {})
    first = summary["milestones"][0]
    assert first["title"] == "Automation science" and first.get("start") is True and first["state"] == "done"


def test_space_age_progress_continues_through_planets_to_the_last_pack():
    data = json.loads(json.dumps(DATA_20))
    data["technology"]["planet-discovery-vulcanus"] = tech(
        ["rocket-silo"], ["chemical-science-pack"], essential=True,
        effects=[{"type": "unlock-space-location", "space_location": "vulcanus"}])
    data["technology"]["metallurgic-science-pack"] = tech(
        ["planet-discovery-vulcanus"], ["chemical-science-pack"], essential=True, effects=unlock("metallurgic-science-pack"))
    data["technology"]["foundry"] = tech(["metallurgic-science-pack"], ["metallurgic-science-pack"])
    data["technology"]["big-mining-drill"] = tech(["foundry"], ["metallurgic-science-pack"], essential=True)
    data["recipe"]["metallurgic-science-pack"] = recipe("metallurgic-science-pack")
    tree = rc.Tree(catalog(data, mods={"base": "2.0.77", "space-age": "2.0.77"}))
    assert tree.space_age
    assert "planet-discovery-vulcanus" in tree.path and "foundry" in tree.path
    titles = [m["title"] for m in tree.summary(set(), None, None, {})["milestones"]]
    assert titles.index("Vulcanus") < titles.index("Metallurgic science") < titles.index("Big mining drill")
    assert tree.summary(set(), None, None, {})["goal"] == "Big mining drill"


def test_cycles_and_unknown_prerequisites_do_not_break_the_tree():
    data = {"technology": {"a": tech(["b", "missing"], ["red"]), "b": tech(["a"], ["red"]),
                           "rocket-silo": tech(["a"], ["red"])}, "recipe": {}}
    tree = rc.Tree(catalog(data))
    assert tree.path == {"a", "b", "rocket-silo"}


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(schema="other"),
    lambda d: d.update(version="2.0"),
    lambda d: d.update(technologies={}),
    lambda d: d["technologies"].update({"bad name": {}}),
    lambda d: d["technologies"]["a"].update(prerequisites=["ok", 5]),
    lambda d: d["technologies"]["a"].update(ingredients=[{"name": "<script>"}]),
])
def test_malformed_catalogs_are_rejected(mutate):
    raw = {"schema": rc.SCHEMA, "version": "2.0.77", "mods": {"base": "2.0.77"},
           "technologies": {"a": {"prerequisites": {}, "ingredients": []}}}
    rc.parse(raw)  # the unmutated form (with Factorio's {} for empty lists) is valid
    mutate(raw)
    with pytest.raises(ValueError):
        rc.parse(raw)


def test_state_and_trigger_values_are_bounded():
    raw = {"schema": rc.SCHEMA, "version": "2.0.77", "mods": {"base": "2.0.77"},
           "technologies": {"a": {"trigger": {"type": "craft-item", "item": "x y", "count": float("nan")}}},
           "state": {"tick": 3, "researched": ["a"], "current": "unknown", "progress": 7}}
    parsed = rc.parse(raw)
    assert parsed["technologies"]["a"]["trigger"] == {"type": "craft-item"}
    assert parsed["state"] == {"tick": 3, "researched": ["a"], "current": None, "progress": None}


def test_export_and_atomic_write(tmp_path):
    raw = {"schema": rc.SCHEMA, "version": "1.1.110", "mods": {"base": "1.1.110"}, "technologies": {"a": {}}}
    sent = []
    result = rc.export(lambda command: sent.append(command) or json.dumps(raw))
    assert sent[0].startswith("/sc ") and "research_catalog" not in sent[0][:4]
    path = tmp_path / rc.FILENAME
    assert result == raw
    rc.write(path, result)
    assert rc.read(path)["version"] == "1.1.110"
    # The sidecar keeps the raw export, so a read rebuilds the same tree.
    rc.write(path, raw_catalog(DATA_20))
    assert rc.Tree(rc.read(path)).packs == rc.Tree(catalog(DATA_20)).packs != []
    assert not [p for p in tmp_path.iterdir() if p.name != rc.FILENAME]
    with pytest.raises(RuntimeError):
        rc.export(lambda command: "Cannot execute command. Error: nope")


def test_sidecar_export_never_raises(tmp_path, capsys):
    class Client:
        def send_command(self, command):
            raise ConnectionError("gone")

    class Instance:
        rcon_client = Client()

    class Backend:
        _instance = Instance()

    assert rc.export_sidecar(Backend(), tmp_path) is None
    assert rc.export_sidecar(object(), tmp_path) is None
    assert "not exported" in capsys.readouterr().err


def test_cli_refuses_a_password_on_the_command_line(monkeypatch):
    monkeypatch.delenv("JEV_RCON_PASSWORD", raising=False)
    with pytest.raises(SystemExit):
        rc.main(["--out", "x.json"])


def write_catalog(path, data=DATA_20, researched=None):
    rc.write(path, raw_catalog(data, researched=researched))


def test_monitor_summarizes_research_from_telemetry_and_reloads_the_sidecar(tmp_path):
    events, sidecar = tmp_path / "events", tmp_path / rc.FILENAME
    with EventWriter(events) as writer:
        writer.emit("goals", 3, goal="rocket_launch", target="rocket_launch",
                    completed_goals={"stockpile_fuel": 1, "bootstrap_mining": 2})
        writer.emit("observation", 2, state=project_state({
            "tick": 50, "researched": ["electronics", "automation-science-pack"],
            "factory": {"research": "automation", "research_progress": 0.5}}))
        monitor = Monitor(events, research=sidecar)
        monitor.poll()
        assert monitor.snapshot()["view"]["research"] == {"status": "waiting"}
        write_catalog(sidecar)
        monitor.poll()
        view = monitor.snapshot()["view"]
        assert view["research"]["status"] == "ok" and view["research"]["source"] == "telemetry"
        assert view["research"]["current"]["name"] == "automation"
        assert view["research"]["current"]["progress"] == 0.5
        assert [m["key"] for m in view["milestones"]] == [
            "stockpile_fuel", "bootstrap_mining", "automation-science-pack", "logistic-science-pack",
            "chemical-science-pack", "rocket-silo", "rocket_launch"]
        assert [m["state"] for m in view["milestones"]][:4] == ["done", "done", "done", "next"]
        writer.emit("observation", 2, state={"tick": 90, "researched": ["electronics", "automation-science-pack",
                                                                        "automation", "logistic-science-pack"]})
        monitor.poll()
        logistic = [m for m in monitor.snapshot()["view"]["milestones"] if m["key"] == "logistic-science-pack"][0]
        assert logistic["state"] == "done" and logistic["tick"] == 90
        sidecar.write_text("{not json")
        os.utime(sidecar, ns=(1, 1))
        monitor.poll()
        assert monitor.snapshot()["view"]["research"] == {"status": "invalid"}
        # Without a usable tree the previous fixed milestone list is shown.
        assert monitor.snapshot()["view"]["milestones"][2]["key"] == "steam-power"


def test_monitor_refuses_to_mix_a_tree_from_another_version(tmp_path):
    events, sidecar = tmp_path / "events", tmp_path / rc.FILENAME
    write_catalog(sidecar)
    with EventWriter(events) as writer:
        writer.emit("observation", 2, state={"tick": 5, "researched": ["automation", "steam-power-from-elsewhere"]})
        monitor = Monitor(events, research=sidecar)
        monitor.poll()
    research = monitor.snapshot()["view"]["research"]
    assert research["status"] == "version mismatch" and research["unknown"] == ["steam-power-from-elsewhere"]


def test_catalog_state_is_used_when_telemetry_has_no_research(tmp_path):
    # An external FLE setup: only the exporter's --watch state is available.
    events, sidecar = tmp_path / "events", tmp_path / rc.FILENAME
    write_catalog(sidecar, DATA_11, researched=["automation"])
    events.write_text("")
    monitor = Monitor(events, research=sidecar)
    monitor.poll()
    view = monitor.snapshot()["view"]
    assert view["research"]["source"] == "catalog" and view["research"]["path_done"] == 1
    # No controller goals are reported, so no goal rows are invented.
    assert all(m["kind"] == "research" for m in view["milestones"])


def test_legacy_records_take_current_research_from_decision_facts():
    record = project_record({"action": "observe", "state": {"tick": 7, "researched": ["automation"]},
                             "decision": {"state": {"facts": {"factory": {
                                 "research": "fluid-handling", "research_progress": 0.4, "entities": {"x": 1}}}}}})
    research = record["state"]["mission"]["research"]
    assert research == {"name": "fluid-handling", "progress": 0.4}
