# Solid-route resume review follow-up

Source-only correction for the three post-merge PR #112 review findings. Native
Factorio acceptance and independent review of this follow-up remain required.

## Validate before attaching

Resume now invokes the actual composed memory loader before `enable_factory` or
any solid Lua installation. Base schema/counters, pending identity, commitments,
actor epoch and declared treatment must all validate. The saved session supplies
a schema binding, not permission to adopt a live session. The first ordinary
observation still checks the current session, tick, ownership and receipts.

Checkpoint bytes must be unchanged across preflight and before the first
observation. This detects intervening replacement; it is not a filesystem lock or
permission for multiple concurrent writers. Memory initialization remains in the
ordinary observer, preserving initial reconciliation of retained uncertain work.
No source file, checkpoint, history, receipt, failure budget or runtime is reset.

## Retain a first-observation fault

An invalid first envelope has no validated epoch. Before an inner observer can
save, record `status=uncertain`, empty `solid_epoch`, empty `solid_commitments`, and
reason `Solid-route epoch unbound; native reconciliation required`. The extension
loader permits exactly this explicit unbound fault, retaining all otherwise valid
base memory. Partial epochs, paid commitments without an epoch, and ordinary
running states with empty epochs are still rejected.

Offline inspection/recovery tools can load that evidence. The constructor refuses
to resume it **before backend attachment** until an operator-owned reconciliation
exists. A subsequently healthy envelope is not authorization to adopt native
ownership. No new reconciliation command or native adoption path is added here.

## Match the manifest snapshot

`RunConfiguration` was already frozen; normal field assignment was not a
reproduced defect. The input object and public property nevertheless shared one
instance. The writer now stores a fresh frozen value from the validated, redacted
manifest, and the getter returns another detached frozen value. Explicit Python
object-mutation bypasses on a caller's input or returned snapshot therefore cannot
change the writer's treatment. This is not a security boundary against arbitrary
code with access to the writer's private internals or process memory.

## Regression coverage and evidence boundary

`tests/test_solid_review_boundaries.py` covers invalid base/extension checkpoints,
zero backend calls on rejection, malformed first envelopes and early saves,
reloadable but non-resumable unbound faults, rejected invalid empty-epoch variants,
checkpoint replacement, uncertain receipt reconciliation, frozen assignment,
input/property detachment and validated-manifest redaction equivalence.

The original nineteen-case reproduction on the exact PR #112 merge yielded
18 failures and one existing frozen-assignment pass. Additional defensive tests
were added afterward. Run the focused file and full Linux suite on the final
combined source. These tests use deterministic backends; no real Factorio run,
provider call, deployed SHA or native improvement is implied.
