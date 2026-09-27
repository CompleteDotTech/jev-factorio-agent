# Committed solid-corridor footprint isolation

This is an implementation correction for #100 and the shared #101/#102 transport
foundation. It is not native Factorio flow or acceptance evidence.

## Defect and boundary

Two different, valid explicit intents can have unbuilt straight corridors that
cross. Checking only current entities and paid components lets both projects
prepare and spend on downstream components before they obstruct each other's
remaining path. Sequential actor calls alone do not prevent this inter-project
conflict when the planner changes its selected project between components.

A successful native prepare now reserves **every planned component cell** of its
corridor, with one tile of Manhattan clearance to prevent unsupported joins.
The bound is conservative: it may reject close parallel layouts that a more
capable transport contract could support. No arbitrary graph routing, crossing,
underground belt, splitter, shared endpoint or rerouting capability is added.
The existing maximum of four routes and 24 belts per corridor remains unchanged.

Unpaid proposals remain alternatives and may overlap. Their order does not grant
an invisible priority. Only successful prepare (after ordinary qualification and
whole-kit affordability) creates a reservation. Once one project is reserved,
conflicting proposals are withdrawn on observation and their diagnostics report
`reserved_corridor`. A stale direct prepare is independently rejected before any
second project is committed or paid, even without an intervening observation.

## Recovery and ownership

Prepared, dispatching, placed, paid, complete and faulted commitments retain their
whole footprint. Ambiguous dispatch or loss of power is not permission to release
it. Existing receipt reconciliation and paid ownership are preserved; this change
never cancels a project, edits a world, refunds items or resets failure history.
The ordinary authorized reconciliation process remains necessary to resolve an
unusable retained project. Other spatially independent routes can still build
interleaved and replay exact paid receipts without another payment.

The Python snapshot validator rejects an inconsistent mixture of committed and
conflicting routes. The durable checkpoint loader applies the same geometric
invariant before enabling the native backend. The composed observer fails closed
without clearing prior failure history. Native observation also faults mutually
inconsistent retained commitments, retaining pending evidence and preventing
further dispatch. These checks do not replace identity, epoch, recipe, power,
collision, whole-kit, ordinary fair placement or receipt checks.

## Installation and operational handoff

The native wire protocol remains 1. The installed implementation revision is 4,
with `reservation_contract=full-corridor-manhattan-v1`. Both are checked before
reattaching an existing runtime, along with the existing contract family and
observer/transfer/configure bindings. A coincidentally equal revision in another
continuation does not authorize a semantically different installed extension.

Earlier revisions are deliberately **not upgraded in place**. Do not clear native storage,
replace the observer behind the running controller, reset the campaign, discard
pending receipts or alter its immutable treatment/cutoff to install this change.
Before native use, reconcile this exact source with the coal-source and downstream
continuations, obtain independent review and normal signed publication, and use
the established service-owner deployment and campaign-preserving handoff. This
patch provides no migration or new operational authority.

## Validation boundary

`tests/test_solid_corridor_reservations.py` covers overlapping alternatives,
crossing and adjacent footprint conflicts, all retained phases, order symmetry,
separate-project controls, pre-install checkpoint rejection, composed admission,
pre-payment native refusal, stale offers, failed-affordability non-reservation,
retained-state corruption, exact reattachment and receipt replay. Lua runs against
deterministic API-shape doubles, not the Factorio engine. Separate paid corridors
with zero delivered items remain zero-flow evidence in the tests.

Native acceptance still requires real paid coal delivery to multiple consumers,
paid downstream ingredient flow, restart/reconciliation tests, matched latency
and resource measurements and #92's original authorized useful-progress window.
Neither passing fixtures nor this reservation contract establishes those results.
