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
`mutation_authorized=false`. A goal-bound net material deficit,
receipt-bound burn or validated active-rate semantics, and same-RPC first
payment recheck remain necessary before positive demand can enter admission.

The v4 fixed query adds the selected technology's research-unit count,
ingredient amounts, current technology-price multiplier and multiplier-ignore
flag, plus each configured owned furnace recipe's deterministic item inputs
and products. All bill rows are bounded, sorted, and decoded with the current
research, lab, furnace unit, burner, graph and fuel in one native RPC. Fluid,
probabilistic, quality-changing, missing and oversized bills remain unsupported.
This is a material-bill *observation*, not a required-work deficit: it does not
subtract all available stock, exclude alternate production, bind an immutable
goal, forecast future burn, or authorize construction. Native 2.0.77 field
compatibility and a positive bill still require an isolated runtime readback.

The v5 fixed query extends that same read-only RPC with the connected actor's
main inventory and handcraft queue, each proposed fuel target's full fuel
inventory and furnace output inventory, the selected technology's research-unit
energy, and each active furnace recipe's energy and current crafting speed.
The decoder binds these rows to the graph's actor, exact owned target units and
coal counts. A non-coal item in a selected target's fuel inventory is still
rejected by the underlying fuel graph; non-coal actor stock is retained, not
silently treated as absent. All inventory rows are bounded, normal-quality and
unique. Missing inventory or unsupported energy/speed yields no projection.
`material_scope.closure_complete` is fixed to **false** by both producer and
decoder. This scope excludes other owned outputs and buffers, unregistered
stock, belts, in-flight/queued production contents, future harvesting or
alternate supply, and any goal-to-technology commitment. It cannot yield a
positive net deficit or native payback. The 2.0.77 API shape and actual stock
readback remain unqualified until an isolated native trial; no live profile is
installed by this source change.

`planning/coal_goal_alignment.py` provides a read-only relevance predicate for
the current rocket goal. It binds the controller checkpoint, snapshot and
decoded native research to one session, tick and actor, then traverses a fresh
bounded catalog prerequisite graph. The selected technology must be on the
rocket-silo research path or on the current locked basic-assembler capability
path, and its catalog science-pack bill must match the native observation.
Malformed, cyclic, changed and unrelated paths fail closed. The predicate
always returns `future_commitment=false` and `mutation_authorized=false`:
the current goal is not a durable promise to complete that research, and
the v5 material scope remains incomplete. No native action or admission gate
calls this predicate yet.

The v3 query introduced a diagnostic manual-row projection, retained by v4
only under a separately source-qualified, additive v5 native profile. The
retained native v4 profile continues to
reject optional connector and journal assets. A one-use owner-locked migration
can install the exact connector ledger and unchanged v1 journal sources and
extend the native manifest only from an exact, quiescent v4 installation with
an explicitly empty connector checkpoint. The migration creates an empty ledger
and registers the journal callback; it does not create or pay for a route.
An older retained v4 checkpoint can instead have `connector_ownership=null`.
That is not an empty protocol-1 binding, and v5 resume refuses to adopt a
native ledger from it. The owner must first run the separate, one-use
`prepare_legacy_empty_connector_checkpoint` transaction while v4 is still
installed. It requires the original single-writer lock, exact checkpoint and
attachment-receipt hashes, a quiescent controller with no open investment or
successor project, exact v4 installed assets, and a read-only native check that
connector handlers and ledger are absent and the actor is idle. It changes
only the saved null field to `{protocol: 1, session_id: ..., routes: {}}` with
the normal fsynced checkpoint writer. A fixed, private intent and original
checkpoint backup are durable before that write; the preparation cannot be
reused. An interrupted or ambiguous result must be classified by
`reconcile_legacy_empty_connector_checkpoint` against the original backup,
receipt and exact expected new bytes. Even an old-checkpoint result consumes
the one-use attempt and requires a separately reviewed recovery. The v5
installer remains strict and must be separately reviewed against the new
checkpoint hash; this preparation never installs an asset or authorizes an
actor action.
The prepared checkpoint is deliberately incompatible with continuing the
old v4 controller: v4 has no native connector ledger to reconcile against the
new empty binding. The owner must keep gameplay paused between preparation
and a separately qualified v5 installation; a failed install requires a new
reviewed recovery plan, not a v4 controller restart.
This source change does not execute that migration. The connector asset also
makes native connector actions available under v5, so separate owner review is
required before any live opt-in.
The migration infers that nth-tick slot 1 is unused from the exact installed v4
source manifest: its known callbacks use slots 5, 15 and 60. Factorio exposes
no documented getter for the slot-1 handler, so this is not direct runtime
introspection and cannot protect an unmanifested ad-hoc callback. Failed or
ambiguous migration responses require read-only manifest reconciliation; they
never authorize automatic retry or gameplay dispatch. Native owner review and
runtime readback remain separate requirements before any opt-in installation.
The source migration now requires a private, checkpoint-bound
`native-manual-cycle-v5.intent.jsonl` on the POSIX owner host. While holding
the exclusive controller lock it fsyncs a `dispatching` record containing the
exact checkpoint/receipt hashes, source-bound before/after manifests, and
command digest before sending the only mutating RPC. The fixed path is never
reused, including when the command throws or acknowledgement is missing.
`reconcile_manual_cycle_v5` takes the same lock and performs only the fixed
readback: exact v5 can be recorded as installed, exact v4 is reported as an
**attempt consumed**, and any other state stops dispatch. It never retries or
deletes the intent. A v4 readback contradicting a durable `qualified`/v5
reconciliation record also stops dispatch. The readback must retain the exact
session, actor, complete module map, source manifests, checkpoint and receipt
hashes in the intent. This source journal is not a guest-side transaction token;
the owner must still schedule a quiescent checkpoint and retain the private
intent and native readback evidence before any opt-in installation.

The original v5 transaction was later found to leave the retained e759
`campaign.observe` closure unchanged. That closure does not emit
`connector_ownership`, even though the v5 connector module and callback
manifest are present. Default reattachment now rejects that exact legacy v5
profile. The one-use v5 observer repair installs a hash-pinned outer bridge
that preserves the factory and solid-route closures and adds the session-bound,
same-tick ledger when the retained observer later emits a factory snapshot.
Its preflight and post-install checks only compare callback identities and
source manifests; they never invoke the observer. It runs under the same
checkpoint lock, requires an empty connector binding and quiescent native
ledger, records its command in a distinct fsynced intent, and binds that intent
to the locked file identity. An ambiguous response is reconciled by readback
only and cannot be replayed. A separate, explicitly journaled native snapshot
probe must confirm the emitted ledger before controller work resumes. The
v5-to-v6 candidate requires this bridged v5 profile.
The query checks the installed source hash, callback identity, journal owner,
complete bounded gather rows, and current retained coal-transfer receipts in
the same RPC as the graph, burner and research state. Its decoder binds target
units and source hash; a separate helper joins selected receipts to verified
controller attempts. Pending, faulty, reordered, stale or rebound rows fail
closed. The projection measures **current** burner/research state and recorded
past actions, not an interval burn delta, a future-demand lower bound, or
proof that the same mined coal supplied the recorded transfers. It explicitly
retains `attempts_bound=false`, `cycle_complete=false`,
`native_payback_proven=false`, and `mutation_authorized=false` until separately
qualified native evidence and payment integration exist.

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
