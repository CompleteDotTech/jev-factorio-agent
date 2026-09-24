"""Explicit rejections proven to precede connection actuation."""


class ConnectionPreflightRejected(ValueError):
    """A known read-only connection check rejected this dispatch."""

    def __init__(self, code: str) -> None:
        if code not in {
            "missing_fluid_port", "no_connection_route",
            "insufficient_connection_materials",
        }:
            raise ValueError("Unknown connection preflight rejection code")
        self.code = code
        super().__init__(code)
