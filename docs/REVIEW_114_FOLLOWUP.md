# PR #114 resume and carried-input follow-up

Refs #100, #102, #103 and the two post-merge review findings on PR #114.
This is source/fixture work, not deployed native acceptance.

## First resumed observation

The composed preflight validates the entire saved memory before native attachment.
The first fresh observation now initializes memory from a detached copy of that
validated value, not a second read of a mutable checkpoint path. The ordinary
controller still checks the actual backend session and non-regressing game tick,
and performs initial receipt reconciliation even for retained terminal states.

Checkpoint bytes are compared before and in a `finally` barrier after the first
resumed observation. Replacement, deletion or an unreadable path prevents adopting
memory or proceeding to a mutation, including when the backend itself raises.
The controller discards the tentative in-process memory and sets sticky execution
and persistence barriers; swallowing the exception cannot allow another step or
save. The other writer's checkpoint is neither overwritten nor repaired. Operator-
owned reconstruction is required. A transient read error with unchanged checkpoint
bytes retains the prior retry behavior; identical-byte atomic file replacement is
compatible. Pending identities, receipts, ownership and failure history are not
reset. The tests exercise a temporary path-swap attack on a hypothetical second
loader as well as ordinary in-flight replacement.

This is not a cross-process lease or filesystem lock. It protects the first-resume
load/observation boundary and prevents adopting alternative bytes there. It does
not grant concurrent writers permission or claim to detect every identical-byte
replacement, later external writer, or arbitrary in-process memory mutation.

## Avoided-hauling demand

The material expansion already credits both carried and collectible stock. Its
recipe batch reconstruction previously forgot which inputs were already carried,
and could value another 120 gears of hauling when 120 unreserved gears were already
in player inventory. It now allocates the shared supply ledger's spendable carried
items once, in stable recipe expansion order, before calculating each ingredient's
remaining handling demand. Reserved and background-locked inventory are not credited;
collectible source output still requires hauling and remains eligible for valuation.
The destination's current input stock and fresh preconditions remain independent
checks. This is a bounded policy allocation, not a new material-spending authority.

Tests cover full/partial/excess carried stock, reservations, multiple recipes sharing
one carried stock pool, destination stock, actual composed-controller admission,
changed inventory before dispatch, and preservation of already paid construction.
Ready science/manual work is retained; no change creates a coal source, bootstrap
network, kit-acquisition planner, or new production CLI/supervisor treatment.

## Evidence boundary

The initial 22-case reproduction on merge `b92e753` yielded 16 failures and six
passing controls. Final exact-source counts and commands are in the delivered
validation record. Tests use deterministic backend objects and local Linux
filesystem behavior, not a native Factorio campaign. Independent final-source
review, existing-identity SSH signing, exact-head hosted checks, normal merge,
authorized deployment and #92's full useful-progress window remain required.
