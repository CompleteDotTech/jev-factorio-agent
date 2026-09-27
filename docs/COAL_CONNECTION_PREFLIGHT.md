# Connection preflight respects committed coal footprints

Generic `factory_connect` constructs ordinary pipes or poles sequentially. Its
complete route must exclude the committed coal network's reserved source and
corridor footprints before its first component is paid. A collision at a later
cell cannot be treated as an ordinary no-effect preflight rejection after an
earlier component has already been placed.

The existing bounded native `_connection_cells` query now calls the coal
extension's `placement_reserved` predicate for each candidate, using the same
prototype and north direction as connection placement. Reserved cells enter
neither the buildable nor existing-cell sets. Both narrow surveys and bounded
fallback surveys use this common query. The planner can select a safe detour or
reject the complete connection before payment. No additional RPC, parallel
actor action, inventory write or receipt change is introduced.

The ordinary `fair.place` reservation assertion remains in force at each paid
build. A survey is not a lock or an atomic multi-placement transaction: subsequent
world changes can still make an action ambiguous and require existing receipt
and ownership reconciliation. This correction does not retry or discard a
previous partial connection. Absent or uncommitted coal proposals leave ordinary
connection availability unchanged.

`tests/test_connection_coal_preflight.py` executes the actual generated probe,
coal reservation predicate and fair cursor-payment Lua on modeled API objects.
Before correction, a reserved later cell caused fixture prefixes of ten pipes
or two poles to be paid before refusal. The corrected cases reject with zero
connection payments; separate cases complete a safe detour and preserve the
absent/uncommitted-coal behavior. These are deterministic contract checks, not
Factorio engine, native travel or throughput evidence.
