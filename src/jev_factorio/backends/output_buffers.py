"""Optional native adapter; ordinary commands retain their original implementation."""
from __future__ import annotations

from ..iteration_timing import decode_native
from importlib.resources import files

from ..output_buffers import COMMAND, PARTS, validate
from ..telemetry import Trace, phase


class OutputBufferFactory:
    def __init__(self, native) -> None:
        self.native = native
        native.command(files("jev_factorio").joinpath("lua/output_buffers.lua").read_text())

    def __getattr__(self, name):
        return getattr(self.native, name)

    def execute(self, action: str, parameters: dict, *, trace: Trace | None = None) -> str:
        if action != COMMAND:
            if trace is None:
                return self.native.execute(action, parameters)
            return self.native.execute(action, parameters, trace=trace)
        from fle.env import Position

        validate(parameters)
        # Preparation freezes observed geometry but creates no game entity.
        # The controller has already checkpointed this exact command as pending.
        with phase("entity_lookup", trace):
            target = decode_native(self.native.call("prepare_output_buffer", parameters))
        with phase("approach", trace):
            self.native.backend._fair.approach(Position(**target["position"]), PARTS[parameters["part"]])
        # Place + register + paid receipt in one native RPC; never retry here.
        with phase("transfer_rpc", trace):
            self.native.call("build_output_buffer", parameters)
        return "Paid buffer component returned; placement and flow require observation"
