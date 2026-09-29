# Closed-world coal producer candidate (#101)

This document describes a source candidate, not a native qualification or
payment authority. The retained v4 observer, optional v5 journal, and closed
admission gate remain unchanged. A positive result requires a later versioned
read-only query and journal producer, an isolated 2.0.77 trial, and a separate
same-RPC first-payment implementation.

The current private prototype composes the fixed v5 graph command with a v6
owned-surface material census. Its decoder reports only
`owned_surface_coverage_complete`; it does not set the v5
`material_scope.closure_complete` flag or infer a goal-linked deficit. The
query rejects a second surface, unregistered player-force/stock-capable
entities, ground items, trees, handcraft queue, cursor stock and active
machine crafts. It enumerates bounded entity and actor inventories, belts,
inserter hands and target fuel/output in the same RPC as the graph. Actual
2.0.77 API behavior remains unqualified by source tests.

The private v2 cycle journal brackets one v1 gather and two-to-four ordered
native coal insertion receipts, samples delivery walking, checks actor coal
conservation and refuses changed units/receipts. The fixed v6 query rechecks
every completed row against the unchanged v1 gather and native transfer ledger
in the same RPC as the stock census and graph; the decoder binds those rows
again to the typed v5 graph. Its sampled work must not be
used as an avoided-work forecast yet: unobserved same-tick actions and route
detours are not excluded by the current journal. In particular, it does not
claim source lineage or a necessary lower bound on future actor work. The
additive v5-to-v6 install candidate requires an exact, empty and quiescent v5
readback, a separate read-only guest preflight, a fixed fsynced one-use intent,
and exact v6 readback. The v2 asset replaces v5 nth-tick slot 1 with a
source-qualified composite that invokes the original v1 gather handler then
the v2 delivery handler every tick; rollback restores the exact v1 handler.
Factorio readback does not directly introspect the nth-tick registration, so
native qualification must additionally observe both handlers progressing on
successive game ticks under this profile.
Failed or ambiguous installation consumes the attempt without replay.

## Evidence boundary

The v5 query observes the actor and selected fuel targets, but explicitly
reports `material_scope.closure_complete=false`. The v1 manual journal samples
walking and mining while one gather receipt is active. Its transfer rows record
coal inserted into fuel targets; they do not measure delivery walking, prove
that the inserted coal was gathered in that attempt, or close other stock.
Changing the decoder's boolean would make an unsupported claim.

The next producer should use one fixed native RPC with a new schema. It must
first bind the existing epoch, source manifest, actor, force, surface, paid
connector identity, whole proposed target set, and exact current research.
It then enumerates all player-force entities on that surface with a finite
limit and compares their units to the campaign registry plus separately paid
connector ledger and the actor. A foreign, unregistered, invalid, aliased,
non-normal, or unsupported entity makes the result unqualified. Every supported
inventory, burner remaining energy, assembler/lab/furnace input/output,
container, transport line, inserter hand, and in-flight product must be read
and bounded. No blank or missing inventory is counted as zero. Ground item
entities and other accessible item sources must be explicitly ruled out or
bounded and accounted for, including neutral-force entities. Recipe outputs,
probabilities, quality, spoilage, modules, productivity, and crafting queue
must be deterministic or rejected.

The query must return a separately scoped stock ledger, not a naked
`closure_complete=true`. The decoder recomputes the ledger and binds every
inventory/line to its unit, name, role, source asset, session and tick. It
rejects missing inventory kinds, duplicate contents, unknown held stock,
changed fuel, a selected tech with no owned lab, or any coverage/count mismatch.
For a bounded current-research workload, it subtracts all usable science
packs, plates, ore, fuel and alternative products from the exact remaining
technology and recipe bill. Current `max_usage` remains an upper cost bound;
only a required deterministic craft with a supported minimum burner energy
can yield a positive coal lower bound. A selected tech or a recipe alone is
relevance, not durable goal commitment.

The manual comparator needs a versioned receipt journal covering a whole
gather and each delivery from the same actor. It must record the native
receipt/attempt identity, start/end inventory, delivered unit, active walking,
mining and transfer work, and all intervening coal-affecting actions. The
decoder must prove conservation across the cycle and reject overlapping work,
foreign transfers, alternate coal inflows, pending/faulted rows, reused
receipts, wrong route or unit, and unowned intervals. Idle RPC delay never
counts as actor-busy time. A safe lower bound may count delivery work as zero,
but only after the entire delivered cycle and conservation are established.

## Strict positive fixture

The smallest positive source fixture has two owned targets (boiler and one
furnace), one actor, no foreign or omitted stock, no alternate fuel, a fully
paid and unreserved kit, a selected committed research goal with a measured
science-pack deficit, one deterministic active furnace recipe, and one
receipt-bound gather followed by deliveries to both exact targets. The decoded
ledger must yield a positive net fuel requirement over the bounded horizon,
and canonical arithmetic must show a strict margin after full kit, bootstrap,
power and service cost. The producer's result is still read-only: native
payback/admission/payment stay false until a later same-RPC first-payment gate.

Negative fixtures include: hidden chest or belt stock, dropped item, wood or
other fuel, unregistered machine, queued handcraft, probabilistic or quality
product, missing lab, unrelated technology, stock covering the entire deficit,
recipe that can finish without fuel, unbound gather/delivery receipt, delivery
before gather, duplicate/foreign transfer, stale tick or session, partial paid
route, changed kit reservation, and a lost native acknowledgement.

## Native and transaction gates

A source fixture cannot attest engine compatibility. The owner must separately
read back the retained exact profile and checkpoint under the single-writer
lock, qualify every new API call against pinned 2.0.77, and make any journal
upgrade additive from a quiescent exact v5 state with a fixed durable one-use
intent. Ambiguous install or readback consumes the attempt, with no replay.
Even a source-qualified positive forecast cannot be used by the controller
until a future native command recomputes the whole proof before `q.prepare`
or any kit/corridor first spend and atomically binds the exact pending receipt.
