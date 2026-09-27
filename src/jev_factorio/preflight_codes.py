"""Bounded rejection vocabulary shared by runtime and offline diagnostics."""

CONNECTION_PREFLIGHT_CODES = frozenset({
    "missing_fluid_port", "no_connection_route", "insufficient_connection_materials",
})
