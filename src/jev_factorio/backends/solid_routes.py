"""Explicit experimental solid-route adapter; ordinary fair actions own placement."""
from __future__ import annotations

from ..iteration_timing import decode_native
from copy import deepcopy
from importlib.resources import files
from types import SimpleNamespace

from ..solid_routes import COMMAND, validate, text
from ..telemetry import Trace, phase


def validate_intents(intents) -> list[dict[str, str]]:
    if not isinstance(intents, (list, tuple)) or not 1 <= len(intents) <= 4:
        raise ValueError("Supply one to four explicit solid route intents")
    roles = set()
    for intent in intents:
        if (not isinstance(intent, dict) or set(intent) != {"source", "target", "item", "destination"}
                or any(not text(intent[key], 64) for key in intent)
                or intent["destination"] not in {"input", "fuel"}
                or intent["source"] == intent["target"]
                or roles & {intent["source"], intent["target"]}
                or (intent["destination"] == "fuel" and intent["item"] != "coal")):
            raise ValueError("Invalid or overlapping solid route intent")
        roles.update((intent["source"], intent["target"]))
    return deepcopy(list(intents))


class SolidRouteFactory:
    """Install once, with an immutable explicit intent list; never start a game.

    The factory is attached only by solid_loop_type. It is intentionally not a
    production CLI/supervisor flag until native qualification and handoff exist.
    """
    def __init__(self, native, intents) -> None:
        self.intents = validate_intents(intents)
        self.native = native
        native.command(files("jev_factorio").joinpath("lua/solid_routes.lua").read_text())
        native.call("set_solid_intents", self.intents)

    def __getattr__(self, name):
        return getattr(self.native, name)

    def observe(self, snapshot):
        snapshot = self.native.observe(snapshot)
        data = snapshot.factory.get("solid_routes")
        # Normalize only the native empty-table ambiguity at this wire boundary.
        # Nonempty lists, missing maps and every authoritative value remain strict.
        if isinstance(data, dict):
            if data.get("routes") == []:
                data["routes"] = {}
            if isinstance(data.get("routes"), dict):
                for row in data["routes"].values():
                    if isinstance(row, dict):
                        for key in ("parts", "flow", "pending"):
                            if row.get(key) == []:
                                row[key] = {}
        return snapshot

    def execute(self, action: str, parameters: dict, *, trace: Trace | None = None) -> str:
        if action != COMMAND:
            return self.native.execute(action, parameters, **({"trace": trace} if trace is not None else {}))
        validate(parameters)
        with phase("entity_lookup", trace):
            target = decode_native(self.native.call("prepare_solid_route", parameters))
        if not isinstance(target, dict) or target.get("name") not in {"inserter", "transport-belt"}:
            raise ValueError("Invalid native solid placement target")
        from ..solid_routes import point
        point(target.get("position"))
        if target.get("already_paid") is not True:
            with phase("approach", trace):
                self.native.backend._fair.approach(SimpleNamespace(**target["position"]), target["name"])
            with phase("transfer_rpc", trace):
                self.native.call("build_solid_route", parameters)
        return "Native solid component returned; paid receipt and material flow require observation"
