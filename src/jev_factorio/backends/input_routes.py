"""Native input-route decorator; existing controls own walking and construction."""
from __future__ import annotations

import json
from importlib.resources import files
from types import SimpleNamespace

from ..input_routes import COMMAND, validate
from ..factory_contract import validate_command
from ..telemetry import Trace, phase


class InputRouteFactory:
    def __init__(self, native) -> None:
        self.native = native
        native.command("\n".join("do\n" + files("jev_factorio").joinpath("lua/" + asset).read_text() + "\nend"
                                 for asset in ("input_routes.lua", "production_sites.lua")))

    def __getattr__(self, name):
        return getattr(self.native, name)

    def execute(self, action: str, parameters: dict, *, trace: Trace | None = None) -> str:
        if action == "factory_place" and str(parameters.get("anchor", "")).startswith("cell-site:"):
            validate_command(action, parameters)
            args = [parameters[key] for key in ("role", "name", "anchor")]
            with phase("entity_lookup", trace):
                target = json.loads(self.native.call("prepare_production_site", *args))
            with phase("approach", trace):
                self.native.backend._fair.approach(SimpleNamespace(**target["position"]), target["name"])
            with phase("transfer_rpc", trace):
                self.native.call("build_production_site", *args)
            return "Paid furnace placed at joint production site; component flow requires observation"
        if action != COMMAND:
            if trace is None:
                return self.native.execute(action, parameters)
            return self.native.execute(action, parameters, trace=trace)
        validate(parameters)
        with phase("entity_lookup", trace):
            target = json.loads(self.native.call("prepare_input_route", parameters))
        with phase("approach", trace):
            self.native.backend._fair.approach(SimpleNamespace(**target["position"]), target["name"])
        with phase("transfer_rpc", trace):
            self.native.call("build_input_route", parameters)
        return "Native input component returned; paid receipt and end-to-end flow require observation"
