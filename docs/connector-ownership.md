# Ordinary connector payment attribution

`factory_connect` now prepares a bounded native route before any pipe or pole
payment. The game retains its exact endpoint units, session, actor, force,
surface, geometry, and every connector unit. Each new connector is paid through
the existing fair cursor placement and recorded in the same native command.
Cells that already contained a same-force connector are explicitly `external`;
they never count as paid ownership. The native observation exposes a route as
`owned` only when every cell was paid in that exact route and every retained unit
still matches. Routine observations include a bounded route summary; detailed
unit receipts are read through 64-cell native pages. Topology and actual
power/fluid flow require separate proof.

A lost reply, failed post-build assertion, or changed connector leaves a native
`building`/`fault` row. A new route cannot replace an active row. There is no
automatic adoption, re-payment, or replay. Reconciliation of such a row needs
an explicit reviewed operation using the original receipt and current units.
The ledger is bounded to 128 routes and 1,200 cells per route; admission closes
at the bound. The controller's existing ambiguous-dispatch behavior remains
fail-closed. The receipt is retained in native runtime storage and exposed in
factory observations; it is not yet a separately qualified coal power or fluid
forecast, and it does not make pre-existing connectors owned.

This is a source-only draft. The retained isolated native session was installed
before this module existed. Its checked reattachment must **not** reload
`fair_actions.lua` or `factory.lua` to pick up this change. A separately reviewed,
single-writer additive installation gate must first verify the exact session,
actor, pinned installed callback chain, source hash and absence of a pending
connector transaction, then load only `connector_ownership.lua` and read back
its protocol and function identities. The guarded reattachment currently pins
the earlier `factory.lua` bytes. This branch changes that file, so a fresh
install from this branch would be rejected on its next guarded resume. Do not
merge or use this branch for native play until versioned installed-source
manifests and tests cover both fresh-install/resume and the retained earlier
installation without silent upgrades. No native connector gameplay acceptance
is claimed.
