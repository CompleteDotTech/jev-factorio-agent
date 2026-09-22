# Shared demand and bounded service visits

Ready-work planners reconstruct one bounded supply ledger from each authoritative
snapshot. Carried stock, reservations, collectible outputs, paid machine inputs,
queued products, the current machine batch, and acknowledged craft outputs are
separate categories. Only unreserved carried stock is spendable. Machine inputs
are projected as products for deterministic solid recipes, never credited as
freely available raw resources. Native unit aliases do not multiply supply.
Unsupported fluid or probabilistic production is not projected. Forecast supply
may reduce lookahead gathering; it cannot authorize transfers or verification.

A running acknowledged craft locks its entire output item type under the existing
contract. Its final baseline plus output is credited once for forecasting, not
again for partially completed items already visible in inventory. Controller
reservations remain owned and persisted by the existing dispatcher. Planning
runs only after the foreground commitment has finished; paid background inputs
are absent from carried stock. The ledger itself is reconstructible, not a second
mutable authority or a checkpoint schema change.

For rocket progression the horizon combines the immediate task with at most two
20-pack batches for each of the current research's first eight pack types,
bounded by remaining research and lab stock. Material expansion consumes shared
stock once across all demands. Burner-service shortages are aggregated within a
50-coal horizon. Gather commands retain the existing 50-item limit. A raw shortage
below the collection batch threshold does not cause a speculative dedicated
trip; an actual critical prerequisite still may request one item. Wood retains
its separate single-tree native interaction.

Foreground ready-work candidates may group at most three already-feasible
transfers within one producer/output-buffer cell: collect output, replenish
fuel, and deliver carried recipe inputs. No projected collection funds a later
step. Each step retains an individual receipt, native reach/ownership checks,
write-ahead record, fresh preconditions, and verification. The existing active
plan commits the visit across controller turns and restart. No parallel actor or
new mutation primitive is introduced. Background work stays single-step, and
input-route guards still prohibit manual feeding of owned automated inputs.

Serial scheduling is unchanged. There are no new flags or weakened safety gates.
Missing or invalid forecast information must not be interpreted as spendable
inventory. This is a bounded operational horizon, not an unlimited rocket bill
or a proof of optimal throughput.

## Validation

`tests/test_demand_service.py` covers supply aliases, reservation validation,
partial craft completion, unsupported recipes, shared science demand, paid
machine supply, critical versus speculative one-item trips, service grouping,
background restrictions, and real-controller continuation after an ambiguous
transfer acknowledgment. The test backend is synthetic. Full hosted CI, including
Lua and browser checks, is required before merging. Native performance remains
an independent experiment: report service trips, carried quantities, starvation,
travel per delivered item, and milestone time under matched starting states.
