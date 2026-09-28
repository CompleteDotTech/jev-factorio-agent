"""Sequential native attachment for the explicitly configured coal source contract."""
from __future__ import annotations

from importlib.resources import files
from types import SimpleNamespace

from .. import coal_supply as coal
from ..iteration_timing import decode_native
from ..solid_routes import point
from ..telemetry import Trace, phase
from .solid_routes import SolidRouteFactory


class CoalSupplyFactory:
    """No game startup, CLI opt-in, provider call or automatic runtime migration."""

    def __init__(self, native, targets, *, coal_economic_admission: bool = False) -> None:
        if type(coal_economic_admission) is not bool:
            raise ValueError("Coal economic admission must be an explicit boolean")
        self.coal_economic_admission = coal_economic_admission
        self.targets = coal.validate_targets(targets)
        current = native
        while current is not None and not isinstance(current, SolidRouteFactory):
            current = getattr(current, "native", None)
        if current is None:
            raise ValueError("Coal supply requires an explicitly bound solid-route attachment")
        coal.validate_transport_intents(self.targets, current.intents)
        self.native = native
        native.command(files("jev_factorio").joinpath("lua/coal_supply.lua").read_text())
        native.call("set_coal_targets", self.targets)
        native.call("set_coal_admission_evidence", coal_economic_admission)

    def __getattr__(self, name):
        return getattr(self.native, name)

    def observe(self, snapshot):
        snapshot = self.native.observe(snapshot)
        data = snapshot.factory.get("coal_supply")
        if isinstance(data, dict):
            if data.get("sources") == []:
                data["sources"] = {}
            if isinstance(data.get("sources"), dict):
                for row in data["sources"].values():
                    if isinstance(row, dict):
                        for key in ("parts", "flow", "pending", "manual_pending"):
                            if row.get(key) == []:
                                row[key] = {}
        return snapshot

    def execute(self, action: str, parameters: dict, *, trace: Trace | None = None) -> str:
        if action != coal.COMMAND:
            return self.native.execute(action, parameters, **({"trace": trace} if trace is not None else {}))
        coal.validate(parameters)
        with phase("entity_lookup", trace):
            target = decode_native(self.native.call("prepare_coal_source", parameters))
        if not isinstance(target, dict) or target.get("name") != coal.PARTS[parameters["part"]]:
            raise ValueError("Native coal source returned the wrong placement prototype")
        point(target.get("position"))
        if target.get("already_paid") is not True:
            with phase("approach", trace):
                self.native.backend._fair.approach(SimpleNamespace(**target["position"]), target["name"])
            with phase("transfer_rpc", trace):
                self.native.call("build_coal_source", parameters)
        return "Coal placement returned; exact paid ownership requires fresh observation"
