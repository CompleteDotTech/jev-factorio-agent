# Optional owner review between steps in one process

The existing one-step native wrapper remains the default. A reviewed owner may
opt into `--owner-step-gate-dir` only for a resumed hierarchical FLE controller
with an inherited single-writer lock, original native attachment receipt, and a
bounded `--steps` value from 2 to 10. The first step still needs its normal
external preflight and authorization. The gate never creates a second process,
grants authority automatically, or retries a failed action. It cannot be used
with `--setup-timing-file`, whose timing schema covers one step.

The CLI additionally requires `--owner-step-lock-path`,
`--owner-step-lock-fd`, and `JEV_NATIVE_ATTACHMENT_RECEIPT`. The parent owner
keeps the original flock alive and passes that descriptor to the child. The
gate directory must be absent and have a private existing parent. A restart
must use a new directory only after reconciling the prior attempt; an existing
directory is never resumed.

After a verified step, the process checks that the in-memory and durable
checkpoint agree, that no pending/attempt/background/transfer/reservation
ownership remains, and that the provider and native actor are idle. It reads
the installed native manifest, current paid receipt ledger and original
attachment receipt, then fsyncs a
private `step-NNNN-request.json` with schema `jev.owner-step-gate.v1`. The
request names the exact checkpoint, receipt and manifest digests, session,
source commit/tree, most recent durable attempt-outcome digest, native paid
receipt-ledger digest, process PID/start tick, sequence, and fresh process nonce.
It declares a quiescent waiting boundary. It contains no model prompt, raw
Lua, endpoint, credential, or arbitrary error text. The process keeps the
original flock while waiting, so reviewers inspect the request, checkpoint,
and native state read-only without acquiring a second writer lock.

An external owner may create exactly one private, exclusive, file-and-directory
fsynced `step-NNNN-grant.json` after review. Its JSON must contain exactly the
request identity fields plus `"decision": "continue"`; request-only fields
`phase`, `verified`, `actor_idle`, and `automatic_continuation` are excluded.
The grant is cooperative authority from the existing owner, not cryptographic
authentication against a malicious same-user process. No grant, a stale or
malformed grant, or a 120-second timeout ends the process without starting a
second step and returns a nonzero process outcome. A timeout writes a durable
`step-NNNN-closed.json`; an accepted
grant records content-free gate wall and process CPU time, including the owner
wait. These durations do not claim network, game or CPU-pressure attribution.
Before accepting a grant, the process rechecks the lock path and
descriptor, source checkout, checkpoint, receipt, provider and native idle
readback, and manifest hash. It then fsyncs `step-NNNN-accepted.json` *before*
entering the next step. A crash after acceptance is ambiguous until the owner
reconciles the accepted record, checkpoint, native receipt and gameplay log.

Terminal, blocked, uncertain, nonverified and unresolved background outcomes
never request a continuation grant. The gate does not relax the controller's
ordinary observe, precondition, action, postcondition, checkpoint and receipt
order. These source tests are offline contract checks; deploying this feature
requires separate exact-source review, a reviewed native wrapper and a guarded
trial. No live throughput or capacity improvement follows from the tests.

The first implementation also refuses nonempty solid or coal commitments even
when they may be completed. This is deliberately conservative: admitting a
completed commitment requires an independently reviewed native/checkpoint
reconciliation rule. A refusal leaves the durable campaign state intact and
requires an ordinary owner-reviewed one-use continuation.
