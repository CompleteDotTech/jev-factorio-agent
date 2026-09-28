# Coal economic admission evidence, protocol 2

`coal_economic_admission` is an explicit immutable production-treatment v2
opt-in, carried through the CLI, research manifest, gameplay record and
version-2 coal checkpoint. The default
protocol-1 coal snapshot and explicit paid-kit behavior remain unchanged.
Opt-in asks the native coal adapter to emit protocol 2 with one `admission`
object. The decoder requires exact fields and the same session ID, tick,
actor, surface, and force as the parent coal observation. Unknown keys,
missing fields, stale epochs, or a positive qualification claim fail decoding.

The present native object has `protocol=1`, `qualified=false`, and
`reason="electric_conversion_and_construction_cost_unknown"`. The owned
electric drill and inserters have an energized network witness, but the
runtime does not prove an exclusive generator, its coal-to-electric conversion,
other network loads, or a bounded total acquisition and construction cost.
The controller calls the decision-local admission evaluator for a **new**
optional kit and withholds it. Existing paid funding keeps its continuation.
Protocol 1 also defers. No native payback or autonomous admission is claimed.

The Factorio 2.0.77 runtime documentation provides
[`LuaEntityPrototype.get_max_energy_usage`](https://lua-api.factorio.com/2.0.77/classes/LuaEntityPrototype.html)
and [`LuaElectricEnergySourcePrototype.drain`](https://lua-api.factorio.com/2.0.77/classes/LuaElectricEnergySourcePrototype.html).
These can bound some electrical demand but cannot by themselves bound fuel
consumption of a shared network. The discovered slot-B server is 2.0.77 with
base only; no qualified FLE/controller route is installed there. A future
positive protocol must bind observed generator efficiency/fuel provenance,
attributed competing loads, and complete paid acquisition, travel, and build
costs before enabling admission.

The v2 treatment requires `coal_kit_policy=true` and exact checkpoint and
manifest identity. Resume across v1/v2 treatments fails closed. This source
carrier permits qualification attempts; the native object still reports
unqualified economics, so new speculative kits remain deferred. Existing
paid funding cannot be upgraded in place to the v2 treatment.
