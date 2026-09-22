# Joint furnace, mining, route, and output sites

When input-route support is enabled, missing iron/copper furnaces can use a joint
site offer instead of the generic factory grid. The read-only native survey plans
a stone furnace, burner drill, input inserter, bounded belt path, output inserter,
chest, and local standing clearance together. It checks native prototype vectors,
placement feasibility, disjoint footprints, mixed ore, and foreign belt neighbors.
It searches only the already-generated bounded area, at most 128 observed ores,
eight selected resource sites, and 4096 cached placement checks. Failed surveys
are throttled. The supported belt path remains at most 64 tiles. The geometric
search considers bounded rectilinear alternatives; it is not a complete router.
A rejected search therefore means no supported candidate, not proof that no
possible factory layout exists.

Survey creates no entity, ghost, item, terrain, movement, or production. Offers
have explicit epochs. The initial furnace is placed through normal fair reach
and item-paid construction, then registered atomically with its native unit.
Fresh preflight is required before the build RPC; lost acknowledgments remain
behind the ordinary controller's no-replay barrier. The machine postcondition
requires the observed owned site/anchor/unit, not just another same-named furnace.
A second dispatch cannot rebuild an already owned source.

The committed geometry feeds the existing output-buffer and input-route builders.
Those builders still pay for each component and independently commission native
material flow. The construction bill is disclosed before building; the furnace
can be bootstrapped before the full kit exists so initial iron production does
not deadlock. Subsequent investment uses the existing recurring-demand gates,
science belt reserve, and full input-route kit requirement. A furnace placement
or a buffer offer is never a flow certificate.

Generic fair build-site search avoids reserved cell footprints. Existing paid
producers and their ownership are never silently moved, replaced, reassigned, or
removed. An incompatible existing manual cell retains batched manual supply;
its route diagnostics expose missing producer, incomplete output commissioning,
ore outside the supported local survey, or failure to find a supported route.
This change improves new-cell siting, not automatic migration of existing paid
cells. Additional capacity is a separate explicit investment; preserved cells
must not be presented as newly commissioned automation.

The full native evidence is logged. Model facts retain only the offer's state,
reason, position, identity, component counts, bill and fallback, not repeated
component coordinates. Stale, malformed, or replaced owned-site evidence fails
closed in the controller. No checkpoint ownership schema or live deployment is
silently changed. Site functions do not wrap the native observer or event owner;
the existing input observer reads their bounded telemetry, avoiding another
reattachment chain.

## Validation

`tests/test_production_sites.py` executes the actual Lua planners/builders against
synthetic engine fixtures. It covers distant ore, obstruction and mixed resources,
read-only surveys, nonoverlapping footprints, stale offers, native reach, paid
construction, source identity, duplicate dispatch, reservations, reattachment,
and end-to-end three-sample input/output commissioning. Python tests cover the
real planner/contract and adapter's prepare-walk-build sequence, including a lost
response. These fixtures are not a native Factorio throughput benchmark. Run
matched saved-state native trials separately before claiming gameplay speedup.
