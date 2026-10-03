# Explicit paid-selection reconciliation

Issue #297 left two real paid offers for the same source/state: row203 rejected,
row204 had a completed selected response but remained pending when the process
failed before native execution. Ordinary ledger/checkpoint readers continue to
reject this unreconciled checkpoint. Never delete either row or replay a model.

`prepare_paid_duplicate_projection` requires full original checkpoint, sealed
research events/manifest/integrity, complete original native073 proof, a current
fresh native-equivalence proof, a successful source-only handoff proof, and an
explicit supervisor authority verified by native OpenSSH against a full pinned
public trust file. The authority binds exact bytes, original source, actual
merged/deployed target commit/Linux source digest, original writer-lock pin,
ordinary gate source, and model correlation. Unknown target/evidence is HOLD.

The API restores only the research-redacted public bootstrap authorization
hash from native073's mutually matching ownership records. It uses the retained
rank order to reconstruct BOTH the exact old WAL request digest and decision
input digest. It replays recorded answers through unchanged ordinary selection
gates without accessing a provider. The fresh full candidate and evidence must
have the same normalized semantic hash before a proposal is returned.

The projection corrects only row204's offered hashes from the legacy factored
wire representation to full semantic identities, then records its verified paid
selection and imports the complete plan at step0. This is representation repair,
not evidence that the two offers were distinct. Row203 and all other rows stay
unchanged; two billed duplicate batches remain counted. Original row204,
checkpoint/research/native authority, wire/input/source/tick identities, archive,
counters, failures, receipts and all pending native state remain retained. Only
the current recovery source header advances to the exact verified handoff target;
all original attempt source identities remain unchanged. History is appended
without truncating its prior contents. A field-by-field receipt preserves both
original rows, changes, causal duplicate fact and final checkpoint digest.

`projection_bytes_under_held_lock` additionally requires ROOT and proves the
original UID1000 private lock's complete metadata and already-held per-FD Linux
FLOCK WRITE record. It never acquires another lock or writes a checkpoint.
A reviewed signed supervisor must retain all evidence and independently recheck
current source/CP/provider/owner/native-equivalence pins under that original FD,
publish one-use durable prepared evidence, perform exact compare-and-swap CP
replacement with original ownership/directory fsync, and publish the result.
Prepared/CAS/result ambiguity consumes the attempt and requires reconciliation.
The imported plan still requires ordinary fresh observation and native dispatch
preconditions; the selected response is no claim of a completed game action.

Offline fixtures may mock signature verification and use synthetic fresh/source
proofs. Such results qualify the projection logic only, never native authority.
