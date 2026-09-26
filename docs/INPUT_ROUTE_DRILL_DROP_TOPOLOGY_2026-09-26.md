# Mining drill delivery topology

A native burner drill had a nil `drop_target` while its `drop_position` lay inside the first owned transport-belt tile. The previous pointer-only check latched `input_topology_changed` after construction, preventing ordinary fueling and commissioning.

The validator now requires the real drop position to land on the first owned belt tile and rejects any contradictory non-nil target. Exact entity identities, orientations, belt neighbors, inserter targets, and paid receipts remain mandatory. Physical topology does not commission a route: mining, delivery, output conservation, elapsed ticks, and three positive flow samples remain required.

`storage.input_routes.inspect(source)` provides read-only geometry/topology evidence and a specific reason, even when a fault is latched. Observation diagnostics include the latched fault and topology reason. Inspection never clears faults or advances flow counters. Existing incidents require separately audited recovery; loading this source does not authorize or automatically clear them.

Validation: 111 focused input-route tests passed using Lupa, including nil-target delivery, malformed/off-tile positions, contradictory targets, broken neighbors, and preservation of latched faults/counters. These are synthetic engine regressions, not a native throughput measurement.

The full offline Python/Lua suite passed: 2324 passed, 77 skipped (optional dependencies); browser tests excluded here and covered by hosted CI.
