# Owned starter-drill output

The starter drill's `iron_ore_collected` observation is the **current ore in its
output chest**, not a lifetime production counter or carried player inventory.
A chest which is absent from the native campaign role registry cannot supply a
planner pickup merely because that counter is positive.

The additive `bootstrap_output_v1` capability binds one exact drill and its
unique drop-position chest to `bootstrap-output:iron-ore`. Its existing
`fair.place`, campaign observation, transfer and connector callbacks remain
unchanged. Native installation records add the capability asset and profile;
the original attachment receipt remains an immutable historical input.

## Ownership and observation

Future bootstrap construction records native item consumption and actual drill
and chest unit IDs before a paid binding can be created. A retained legacy pair
instead requires a separately reviewed, signed, one-use reconciliation which
pins the campaign, actor, surface/force, original drill, exact chest and drop
geometry, checkpoint, writer lock, source and native identity evidence.

A legacy binding is `legacy_authorized_current_asset`, effective **from now**.
It always reports `historical_paid_placement_proven=false`. Current authorization
does not invent a historical paid placement receipt, adopt another role, or
discover unrelated entities.

The recovery launcher supplies `JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS` and
`JEV_NATIVE_BOOTSTRAP_OUTPUT_WITNESS_SHA256` for the separately verified ownership
witness. The reader requires an immutable regular file owned by the connecting
process, mode0400, an exact nonzero SHA256, a bounded size and stable descriptor
metadata. Schema `jev.bootstrap-output-ownership.v1` binds session, actor,
surface/force, drill/chest IDs and geometry, origin, signed installation authority,
binding tick and capability asset. The current native binding must match it.
This witness does not replace the original native attachment receipt.

A read-only sidecar accompanies the same atomic RCON observation. It reports
the bound output identity, current normal-quality contents and fresh normal
iron-ore insertable headroom in the actor's main inventory. The existing coal
capacity proof is retained. Cross-tick/session/actor, changed endpoint or
authority, ambiguous geometry, malformed counts, unsupported quality and
unqualified ownership are rejected before publishing the observation.

## Planning and transfer

The normal planner collects observed owned output before choosing manual raw
mining. Bootstrap pickups have distinct IDs for their exact unit and quantity.
They are bounded by the current local demand, chest stock, native actor headroom
and the existing transfer limit.

`bootstrap_output_pickup_start_evidence` is a separate typed proof. It requires
the coherent owned binding, a current enabled catalog recipe-input path and an
exact recompilation of the local planner demand. Its immutable ownership digest
binds the endpoint and current authorization without duplicating the native row.
Ordinary production pickup proof still requires its registered recipe product.

The model judges this candidate's evidenced local target, with explicit plan and
evidence pointers. Collection is partial recipe-input progress; it is not a
completed pickup, later recipe output, route flow, blocker removal or historical
payment. Missing or contrary evidence restores the ordinary shared objective.
The usefulness, confidence and benefit gates and request byte limit are unchanged.

Dispatch uses the existing native transfer callback after current endpoint,
normal reach, source stock and actor capacity checks. The real receipt and
same-command player/source quantity deltas must verify before progress counts.

## Interruption and installation recovery

Native prepared installation, placement and transfer records precede effects.
Pending effects fence further actor mutations. Receipt-after-effect reply loss
is reconciled from the captured real receipt and same-command conservation proof,
so later drill production cannot invalidate an already captured delta. A proven
partial transfer keeps its actual receipt quantity and needs explicit exact
outcome reconciliation; it never becomes a full requested transfer.

Missing receipts or ambiguous placement effects remain blocked. Installation
reply loss consumes its transaction nonce. A separately reviewed reconciliation
can finish a supported retained prepared installation after checking the exact
old or new callback/asset transition; it does not reload the asset, repeat a
placement/transfer, or rewrite earlier receipts. The literal `/sc ` prefix plus
the reviewed Lua body is used without a source transformation.

Offline tests and prospective replay prove these source contracts only. A source
merge is followed by signed native installation and fresh receipt-verified
gameplay; neither mocked ownership nor green CI demonstrates native progress.
