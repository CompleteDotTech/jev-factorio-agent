# Native bootstrap output ownership

`iron_ore_collected` is the current starter drill output chest stock. It is not
player inventory or a lifetime production counter. An unregistered same-force
chest is not an owned campaign source. The optional `bootstrap_output_v1`
capability registers only the exact original drill's unique native output chest,
under the role `bootstrap-output:iron-ore`.

Future bootstrap placement uses the retained paid `fair.place` callback and
records each returned native unit, owner identity and one-item inventory delta
before binding. It does not pay for another drill/chest when already bound.
Legacy reconciliation requires a separately reviewed, signed, one-use installer
with exact checkpoint, writer lock, source, original attachment and current
native identity report pins. Its origin is `legacy_authorized_current_asset`:
ownership takes effect at `bound_at_tick`; historical paid placement remains
explicitly unproven. No broad discovery or historical receipt fabrication occurs.

The pure builders in `backends/native_bootstrap_output_migration.py` perform no
RCON, signing or authorization themselves. `command` keeps original callback and
asset pins, checks native quiescence and persists prepared installation authority
before registration. `quiescence_readback_command` runs its exact pre-effect
checks without installing. `ownership_readback_command` reads the retained
authority, native phase, journals and current output. A consumed attempt must not
be dispatched again after an error or missing reply. A newly reviewed
`reconciliation_command` can finish the retained exact prepared capability and
publish its new manifest; missing or changed code/authority stays blocked.

After successful installation and fresh qualified native readback, the signed
operator creates an exclusive, fsynced, owner-readable `0400` witness with schema
`jev.bootstrap-output-ownership.v1`. Its exact fields are `schema`, `session_id`,
`actor_unit`, `surface_index`, `force_index`, `drill_unit`, `chest_unit`,
`drill_position`, `drop_position`, `chest_position`, `origin`,
`authorization_sha256`, `bound_at_tick`, and `asset_sha256`. The separately signed
launcher verifies the full installer prepared/result evidence and pins this
file through `JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS` and
`JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256`. The original native attachment
receipt is retained. The source reads and checks this witness; it does not
substitute that file for signature verification.

The atomic observation appends a read sidecar in the same native command. It
binds current full normal-quality output and separately typed iron-ore actor
capacity to the same tick/session/actor/force/surface. Existing coal capacity
evidence remains intact. Pickup quantities are bounded by current stock, current
headroom, current planner deficit and the ordinary 200-item transfer bound.
Native preflight shares the existing approach lookup RPC, then pickup uses the
retained native transfer callback, ordinary reachability and real receipt.

Placement and transfer journals fence subsequent actor mutation. Transfer
conservation and actual receipt fields are recorded within the same command,
including a callback error after an effect. Later producer output cannot alter
that operation's proof. `reconcile_pending` verifies the recorded effect without
repeating placement/transfer or changing the native receipt. A proven partial
transfer requires explicit exact receipt and actual quantity reconciliation; it
does not become a full requested transfer. Missing identity, paid record,
same-command conservation or receipt remains ambiguous and blocked.

The bootstrap tests use native API doubles and captured source fixtures. They
are offline contract evidence, not a native acceptance run or proof that the
live game has resumed.
