# Connector checkpoint binding

The PR #155 native ledger records a bounded ordinary pipe or pole route before
its first fair placement and retains exact unit numbers after payment. This
follow-on copies the page-verified route into `CampaignMemory.connector_ownership`
on the first fresh observation of a checkpointed pending `factory_connect`.
An unbound/old checkpoint cannot adopt a ledger installed later. The receipt
identity is derived from the plan's immutable source, target, kind and fluid.
On resume the composed checkpoint is validated against the installed native
session before `enable_factory` runs, and the exact bytes are checked again at
the first observation. The separate native installation manifest must reject
absent/changed campaigns before loading any Lua.

On each later observation the controller requires every recorded route to remain
present with the same owner, endpoint and cell identities. Every observation
reads bounded 64-cell native detail pages and validates each paid/external bit
and unit against the saved checkpoint. A fault, omitted route, changed paid unit
or unexpected receipt fails
closed. A partially paid or externally supplied route retains the pending
action and requires exact reconciliation. The controller does not retry the
placement after a lost reply. It clears a pending connection only when the exact
route is complete and every cell was paid by that route. An already recorded
receipt cannot be admitted for a second dispatch.

This checkpoint does not prove electrical or fluid flow, and it never credits a
pre-existing same-force connector as paid ownership. The retained legacy native
installation has no connector ledger and must not gain it through a routine
resume; the versioned installation manifest remains a separate dependency.
