# Bounded native coal economics evidence

`lua/coal_economics.lua` is a fixed read-only projection and
`coal_economic_observation.decode` validates its bounded typed facts. The coal
adapter can now run this fixed read-only query against a current unpaid bundle
and bind paid connector cells to the reconciled checkpoint ledger. It is not
wired to production admission or payment. Successful decoding always
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

The v2 fixed query also reads current research name/progress, the owned lab's
science inputs, and every proposed external furnace's exact role/unit, current
recipe, input, crafting state/progress and burning fuel **in the same RPC** as
the graph and fuel targets. The decoder requires the complete furnace target
set, exact lab membership in the owned electric graph, sorted bounded item
rows, and the same burner identity already checked in the fuel projection.
No current research yields an explicit unavailable record. These are
instantaneous activity facts, not a lower bound on future coal consumption:
the prototype `max_usage` is an upper rate, and `is_crafting()` can remain true
while progress is stalled. Selected research before lab construction yields
`research_lab_unowned` in this projection without failing the graph query.
The read path still returns `native_payback_proven=false` and
`mutation_authorized=false`. A bounded current-research material bill,
receipt-bound burn or validated active-rate semantics, and same-RPC first
payment recheck remain necessary before positive demand can enter admission.

Two independent admission/payment integrations remain essential:

The source-only `coal_current_evidence` helpers now bind a fully carried,
unreserved whole kit to the unpaid v2 snapshot, catalog, and idle controller
checkpoint. All checkpoint reservations and unfinished solid corridor bills
are subtracted; pending transfers, background jobs, and route work refuse the
claim. The helpers may set
acquisition actor ticks to zero only for that case; collectible output and
queued recipes do not count as paid kit stock. They also join each target's
native coal-transfer receipt (role, unit, quantity and tick) to one verified
controller attempt. The resulting delivery evidence explicitly reports
`cycle_complete=false`: current native gathers have no receipt, and attempt
elapsed ticks include API waits, so neither gathered coal nor avoided actor
time can be inferred. The helper does not supply current per-target future
demand. No positive forecast or first payment follows from these records.

`decoded_gather_work` defines a still diagnostic v1 journal contract:
one complete coal gather with the same session, actor, surface, force, native
receipt, and verified controller attempt. It checks a bounded native coal
inventory delta and separately reported walking/mining tick counts. It rejects
elapsed attempt time, partial or pending work, overflow, fault, changed owner,
and counts greater than the native interval. A receipt-bearing `factory_gather`
requires a separately installed, source-qualified `coal_manual_journal_v1.lua`,
which uses an independent `on_nth_tick(1)` callback so the pinned fair `on_tick`
chain stays intact. Ordinary gathers do not install it. The action refuses
journaled work before mining unless the exact optional asset is already present
in a qualified native attachment. The retained v4 live profile does not admit
the optional asset; an explicit, separately reviewed migration is required.
An error or ambiguous RPC
leaves the native pending receipt to reconcile, and an invalid completion is
retained as failed. The existing live treatment has no such producer. Parsing
a caller-supplied row cannot establish its origin or a complete manual cycle;
the decoder leaves producer qualification, cycle completion, and mutation
authorization false. A fixed source-qualified query, restart reconciliation,
native callback qualification, and complete manual deliveries are still
required before counts can enter payback.

- The fixed query admits only complete, wholly paid connector-ledger cells with
  exact native unit, position, owner epoch and endpoint identity. The adapter
  requires a session checkpoint and compares each route against the same-tick
  native connector summary before querying. The query projects bounded route
  identity and endpoint roles/units, which the decoder compares against that
  checkpoint along with every paid cell. Incomplete, rebound, stale or foreign
  routes fail closed. A read-only qualified graph remains evidence, not
  permission to make the first coal payment.
- Manual task/actor-time journals, demand/acquisition forecasts, paid bootstrap
  acquisition and atomic proof-bound first-payment pending/receipt checks are
  separate. Native facts alone do not authorize spending. Existing policy remains
  deferred until those integrations and native trials are complete. This
  connector-bound read path is partial issue #101 source work, not completion of
  coal-network admission or native acceptance.
