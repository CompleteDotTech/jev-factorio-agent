# Bounded native coal economics evidence

`lua/coal_economics.lua` is a fixed read-only projection and
`coal_economic_observation.decode` validates its bounded typed facts. Neither is
wired to production observation, admission or payment. Successful decoding always
retains `mutation_authorized=false` and `native_payback_proven=false`. Synthetic
fixtures establish source behavior, not native profitability or ownership history.

The query reads the existing `jev_fle_runtime` actor, registry and unpaid coal
proposal directly. It never invokes runtime observation or fair callbacks,
initializes a campaign, creates wire connectors, modifies globals/storage, changes
player control, awards items or installs handlers. It uses native getters and
bounded local graph surveys. Root/operator must retain the exact query and raw
response provenance; a JSON digest does not authenticate a game server.

The first supported graph is normal-quality, base-only Factorio 2.0.77: one owned
boiler, one owned offshore pump, one or two owned steam engines, supported electric
loads, normal pipes, and two to four coal targets consisting of the boiler and
stone furnaces. Tanks, underground pipes, switches, foreign power/fluid members,
modules/productivity effects and other shapes remain unsupported. Proposed coal
sources must be the existing unpaid frozen layout with finite exclusive coal and
no intersecting foreign drill. No constructed future coal route is required.

All running members retain native `active`, `status` and read-only
`get_control_behavior()` presence. Inactive or controlled members are refused;
the first version supports no control behavior, even an enabled one. Boiler,
pump and steam-engine must currently report normal, working or full-output status.
An idle consumer may retain one of the explicitly supported non-disabled
statuses while still being priced at its maximum load. Pipes are passive.
These point-in-time fields are future atomic guard inputs, not a guarantee that
control, fuel or fluid availability will remain unchanged after observation.

Power qualification traverses at most 32 registered poles and 128 directed copper
edges. It surveys their supply squares and every existing/proposed electric
footprint's overlapping poles. The decoder validates connected reciprocal edges,
exact coverage sets, common network identity and the union of at most 64 electric
members, with at most 32 existing loads. A single electric_network_id is not used
as proof against overlapping foreign power.

Fluid qualification traverses at most 64 registered entities, eight fluidboxes
per entity and 128 directed links. Segment IDs, reciprocal endpoints, fluid
classification and capacities must agree. `get_capacity` is a whole-segment
capacity; steam buffer energy is charged once per segment using capacity times
200 J/unit/degree times (165-15) degrees. Water must remain at 15 degrees. Electric
buffers include each existing member's live `electric_buffer_size`. Future
drills and inserters use read-only capacity witnesses from already owned,
normal-quality entities of the same type. Every owned witness of a type must
agree; a missing or inconsistent witness leaves the projection unsupported.
The 2.0.77 prototype readback reports `buffer_capacity=0` for some of these
machines, so that field is retained as evidence but is neither an upper bound
for live capacity nor the cost of a future entity. Unknown or missing live
capacities never default to zero. Witnesses are not adopted into the power
graph or credited as coal-route parts.

The strict decoder pins a full unit-conversion record with exact source file and
readback hashes and conversion contents. The verified 2.0.77 base assets specify
90 kW for the drill, 1.8 MW for the boiler, 75 kW for assembling-machine-1, and
60 kW for the lab. The native values are those powers divided by 60 ticks/second.
Inserter movement/rotation cost 5 kJ each and speeds .035/.014 per tick account
for its 245 J/tick maximum; its .4 kW drain is 6.666... J/tick. Steam-engine .5
fluid units/tick times 200 J/unit/degree times 150 degrees gives 15,000 J/tick.
The offshore pump explicitly uses a void source despite exposing a 1,000 J/tick
maximum: it is a qualified fluid producer, not an assumed electrical load.

The native raw prototype readback and installed base data agree on these rates.
This static correspondence does not prove the complete live topology, decoder
API compatibility on a developed factory, power sufficiency, or realized payback.
Those still require actual engine readbacks and paid gameplay.

A bounded read-only inspection of the isolated, pre-gameplay 2.0.77 save on
2026-09-28 found prototype capacities of zero for the drill, inserter, lab,
assembler and steam engine, with no already owned instances of those types.
The private raw receipt is retained by the operator (SHA-256
`0cc50c51b35739e1fb2da23b4e1be5b3788f62c293a066d56d1bdd1567a6c619`).
This inspection establishes the ambiguity and the absence of live witnesses;
it does not yield a positive economic projection for that save.

Conservative integer conversion rounds consumption/drain/buffers up and available
capacity/energy down. Burner inventory, remaining-burning-fuel and burner heat
are retained. Fractional fuel needs **two** stock bounds: floor for bootstrap
credit, ceiling for a demand ceiling. `SourceFacts` preserves both. Before
production integration, the pure forecast input must represent those distinct
directions rather than reuse one rounded stock for both decisions.

The query has a 262,144-byte output cap and emits a plain unsupported reason when
runtime, topology, units or survey budgets do not qualify. Partial diagnostic
rows never become an economic witness. Ordered arrays retain their type; a known
empty-array position may accept the empty-map wire representation.

Two independent ownership/payment integrations remain essential:

- Ordinary `FairActions.connect` currently discards placed pipe/pole unit IDs and
  `factory_connect` does not register them. Paid connector journals and checkpoint
  receipts must establish these exact owners before this graph can qualify.
  Same-force nearby entities are never adopted by this decoder.
- Manual task/actor-time journals, demand/acquisition forecasts, paid bootstrap
  acquisition and atomic proof-bound first-payment pending/receipt checks are
  separate. Native facts alone do not authorize spending. Existing policy remains
  deferred until those integrations and native trials are complete.
