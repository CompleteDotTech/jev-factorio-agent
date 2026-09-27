"""Synthetic coherent owned corridor; quantities and proofs are test inputs."""
from copy import deepcopy
from jev_factorio.state import GameSnapshot
from jev_factorio import solid_routes as r

SOURCE = "recipe:iron-gear-wheel"
TARGET = "recipe:automation-science-pack"
ROUTE = "solid:5001:5002:iron-gear-wheel:input"
INTENTS = [{"source": SOURCE, "target": TARGET, "item": "iron-gear-wheel", "destination": "input"}]


def fixture():
    source = dict(role=SOURCE, unit_number=5001, name="assembling-machine-1", inventory="output",
                  position={"x": .5, "y": .5}, recipe="iron-gear-wheel",
                  bounds={"left_top": {"x": -.9, "y": -.9}, "right_bottom": {"x": 1.9, "y": 1.9}})
    target = dict(role=TARGET, unit_number=5002, name="assembling-machine-1", inventory="input",
                  position={"x": 7.5, "y": .5}, recipe="automation-science-pack",
                  bounds={"left_top": {"x": 6.1, "y": -.9}, "right_bottom": {"x": 8.9, "y": 1.9}})
    specs = [("receive", "inserter", 5.5, 12), ("belt:2", "transport-belt", 4.5, 4),
             ("belt:1", "transport-belt", 3.5, 4), ("send", "inserter", 2.5, 12)]
    row = dict(route=ROUTE, layout="solid-layout:1", item="iron-gear-wheel", source=source, target=target,
               steps=[dict(part=p, name=n, position={"x": x, "y": .5}, direction=d) for p,n,x,d in specs],
               parts={}, state="proposed", topology=False, flow={}, reason="proposal", pending={})
    entities = {e["role"]: {"name": e["name"], "unit_number": e["unit_number"], "position": deepcopy(e["position"]),
                            "recipe": e["recipe"], "input": {}, "output": {}, "fuel": {}, "energy": 100,
                            "products_finished": 0, "crafting": False} for e in (source, target)}
    entities[SOURCE]["output"] = {"iron-gear-wheel": 20}
    return GameSnapshot(session_id="solid-fixture", tick=300, world_kind="mock", researched=[],
                        inventory={"inserter": 2, "transport-belt": 2, "coal": 20},
                        nearby_resources={"coal": 1}, factory={"entities": entities,
                            "player_bound": True, "player_connected": True, "crafting_queue": 0,
                            "receipts": {}, "produced": {}, "solid_routes": {"protocol": 1,
                            "session_id": "solid-fixture", "tick": 300, "actor_index": 1, "surface_index": 1,
                            "force_index": 1, "routes": {ROUTE: row}, "diagnostics": [
                                {"intent_index":1,"state":"proposed","reason":"ready_layout"}]}})


def row(snapshot):
    return snapshot.factory["solid_routes"]["routes"][ROUTE]


def parameters(snapshot):
    value = row(snapshot)
    step = next(s for s in value["steps"] if s["part"] not in value["parts"])
    return {"route": ROUTE, "layout": value["layout"], "part": step["part"], "receipt": f"receipt:{step['part']}"}


def build(snapshot, parameters=None):
    p = parameters or globals()["parameters"](snapshot)
    assert r.allowed(p, snapshot)
    value = row(snapshot)
    s = next(s for s in value["steps"] if s["part"] == p["part"])
    role = f"{ROUTE}:{s['part']}"
    paid = dict(role=role, unit_number=6000+len(value["parts"]), receipt=p["receipt"], paid=1)
    value["parts"][s["part"]] = paid
    value["pending"] = {}
    snapshot.factory["solid_routes"]["diagnostics"] = [{"intent_index":1,"state":"committed","reason":"paid_or_pending_route"}]
    snapshot.inventory[s["name"]] -= 1
    snapshot.factory["entities"][role] = dict(name=s["name"], position=deepcopy(s["position"]),
                                             unit_number=paid["unit_number"], energy=100)
    value["topology"] = len(value["parts"]) == len(value["steps"])
    value["state"] = "ready" if value["topology"] else "building"
    snapshot.tick += 1
    snapshot.factory["solid_routes"]["tick"] = snapshot.tick


def full(snapshot):
    for _ in range(4):
        build(snapshot)


def commission(snapshot):
    v = row(snapshot)
    v["reason"] = "observing_flow"
    v["flow"] = dict(layout=v["layout"], source_unit=5001, target_unit=5002, method="stoichiometric_balance",
                      first_tick=snapshot.tick-180, last_tick=snapshot.tick, last_positive_tick=snapshot.tick, positive_samples=3,
                      sent=3, received=3, unattributed_loss=0)
