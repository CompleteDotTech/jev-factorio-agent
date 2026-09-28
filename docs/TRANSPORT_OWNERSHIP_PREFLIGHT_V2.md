# Read-only transport ownership preflight v2

This protocol checks a single idle boundary against an existing checkpoint. It
does not start gameplay, initialize FLE, attach an actor, acquire a writer lease,
authorize deployment, prove authentic payment, or establish native acceptance.
Passing fixture tests is not a successful engine qualification.

## Explicit selection and compatibility

The private configuration accepted by `python -m jev_factorio.dev_preflight`
retains exactly these fields: `schema`, `vm_uuid`, `production_vm_uuid`,
`session_id`, `port`, and `password_file`. Setting the integer `schema` to `2`
selects the fixed packaged `lua/acceptance_probe_v2.lua` query. Schema `1`
continues selecting the original query and deliberately reports unsupported
solid and coal ownership. Neither configuration accepts an arbitrary command or
host. Existing private-file, distinct guest DMI identity, loopback-only RCON,
checkpoint readback, and response-size checks still apply.

The report schema is `jev-factorio.dev-preflight.v2`. Its ownership scope is
`point_in_time_transport_ownership_not_flow_or_payment_authenticity`.
`gameplay_started`, `deployment_authorized`, and `native_acceptance_proven` remain
false even when `ready_for_coordinated_validation` is true. A report is unsigned
operator evidence; its query digest identifies the expected source and does not
authenticate a server or a fabricated report.

## Boundary and ownership checks

Existing composed checkpoint loaders validate retained schemas and ownership
without contacting a backend. V2 additionally refuses active plans, attempts,
pending mutations, reservations, background jobs, capital investment, solid or
coal funding, transfer recovery, and unfinished successor projects. The actor
must match the checkpoint session and solid/coal epochs, be the sole connected
player, and remain bound to the original character. The current supported engine
is Factorio 2.0.77 with only the base mod, running unpaused at normal speed.

The fixed Lua query reads existing runtime cells, paid part references, receipts,
native entity identity and geometry, and current idle fields. It does not invoke
campaign observation, `fair.actor()`, placement, transfer, callbacks, or handlers.
Reading native `LuaEntity.get_recipe()` is the only entity method used; no game
object is written. Missing runtime state is reported and never initialized.

The bounded projection covers:

- Solid revision 4, protocol 1, exact contract family/reservation contract,
  immutable ordered intents and binding, and up to four frozen commitments.
- Coal revision 4, exact targets, binding, admission opt-in, committed status,
  and up to four frozen commitments. An unpaid proposal may have no paid parts.
- Up to two mining outposts, their frozen geometry/flow and exact four-entry
  maximum receipt map, matched to retained outpost commitments.
- Up to four input routes and five output buffers, matched to input commitments,
  ordinary output commitments, or qualified successor receipts.
- Up to 2,048 owned entity roles and 66 parts per route. Units, roles and paid
  receipts cannot alias across transport families. Each paid unit must still be
  the same current entity in the runtime ownership map. Frozen component
  geometry is checked by the query; retained solid/coal endpoints and steps are
  also checked by the Python report validator.

The isolated 2.0.77 engine encoder probe returned `{}` for empty tables, including
nested `parts` and `commitments`; an initially suspected empty-array failure was
not reproduced. For defensive compatibility with adapter wire representations,
the probe and independent report validation normalize only empty arrays in declared map fields:
owned entities, commitments, receipts, paid parts, and outpost flow. Nonempty
arrays in map positions are invalid. Ordered intents, targets and steps are
never coerced. The stored probe report uses canonical empty objects for those
maps; independent verification also accepts the tested empty-array wire form.

The query fails closed on a faulted cell, untracked paid offer, unknown shape,
wrong revision, invalid entity, or bounded-copy overflow. Copying is capped at
eight levels, 128 entries per copied table and 12,000 copied values. Pending
construction/manual-fuel journals, crafting submission, native walking/mining,
unreconciled fair/craft jobs, and nonempty legacy queues cannot qualify an idle
boundary. Paid empty-prefix commitments remain meaningful retained owners; they
are not silently dropped. This check does not establish transport flow, handler
integrity, a future admission decision, or the authenticity of transfer history.

`complete_capture` and independent verification recognize two explicit report
paths. V1 preserves its unsupported-ownership evidence requirement. V2 requires
the fixed query digest, exact non-authorization fields, a fresh revalidation of
the embedded native projection against the initial checkpoint, and matching
session/actor/surface/force/mod identity throughout the captured rows. Gameplay
must not precede the probe tick. Both paths still produce `native_acceptance:
not_accepted`; ordinary acceptance criteria remain separate.

## Durable ordinary output-buffer ownership

`OutputBufferMixin` now uses the explicit checkpoint extension
`output_buffers_schema: 1` and `output_commitments`. Each ordinary source retains
its source unit, layout, and exact paid part roles/units/receipts. The map owns
only `recipe:iron-plate`, `recipe:copper-plate`, and `recipe:steel-plate` outputs.
Growth output ownership stays in `successor_receipts`; it is validated alongside
ordinary owners without duplicating an evolving paid prefix. Units and receipts
cannot alias between the two owner families. A growth entry in the new ordinary
map is rejected, even when it copies an existing successor receipt.

Every observed paid addition must match one active pending
`factory_buffer_build` command, source/layout/part and prepared attempt receipt.
A lost response can therefore reconcile the same paid entity without another
payment. All rows validate before installing an updated ordinary owner map, and
the checkpoint save precedes further mutation. Save failure poisons the
controller. The dynamic save call preserves the outer solid controller's first
resume transaction. A rejected buffer observation cannot advance the outer
successor receipt owner.

Legacy read-only checkpoint inspection retains its original schema and never
invents owners. Explicitly enabling the extension on a legacy controller is
allowed only at an idle, reconciled running boundary before backend
initialization. It begins with no ordinary owners. Existing successor receipts
remain their existing durable authority; existing ordinary paid parts without
retained provenance are refused. The original file is not changed by loading.
The preflight reports `ordinary_output_ownership_not_retained` for any unmatched
ordinary buffer, even if its live parts appear valid.

V2 matches the new durable ordinary map and the separate successor map to its
fixed native projection. Capture verification, offline diagnostics and supervisor
validation retain the extension and reject paid ownership regression. These
source checks close the missing ordinary ownership field; actual paid engine
construction, restart qualification and complete acceptance remain separate
evidence requirements.

## Validation status

Dedicated tests run the fixed query against actual solid, coal, and outpost Lua
extensions over explicitly synthetic engine fixtures; test callback refusal,
paid identity/geometry, receipts, pending work, overflow, and unchanged runtime
counters; and exercise capture/verification with checksum-consistent altered
reports. Legacy v1 tests remain in the regression set. These tests establish
source behavior only. Qualification on the isolated actual engine must use an
already legitimate connected actor and genuinely paid retained ownership under
the existing single writer; this protocol is not bootstrap authorization.
