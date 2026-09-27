# First resumed solid observation: checkpoint publication barrier

Refs #100, #95 and #103. This is a source correction after PR #117, not native
acceptance, a production deployment, or a new runtime treatment.

## Reproduced boundary

PR #117 validates captured checkpoint bytes and initializes from that immutable
capture. Its comparison enclosed `_observe_snapshot()`. The real input-route,
background-job, mining-outpost and successor observers do additional validation
and `_save()` calls after that method returns. A checkpoint replaced or deleted
in that interval could therefore be overwritten by a provisional reconciliation.
A later exception could also leave unaccepted observation state on disk.

## Correction and durability

The outer solid controller retains the immutable capture across the complete first
resumed `_observe()` composition. Inner `_save()` calls are deferred only while
this initial read-only observation is active. Action admission is blocked during
that interval. After every observer has returned and the captured bytes are checked
again, the reconciled state crosses the ordinary synchronous checkpoint barrier
before any actor action is allowed. Ordinary observations, dispatch, prepared and
pending action state, receipts and subsequent ownership saves are not debounced.

If an exception follows tentative memory initialization, that memory is discarded
and execution/persistence become sticky-failed. Replacement or deletion preserves
the other writer's bytes or absence. Restoring the original file does not permit
reuse of the failed controller. Reentrant observation is rejected before a second
backend read. A transient read failure before memory initialization retains PR
#117's retry behavior only when the captured checkpoint remains unchanged.

A final flush failure uses the existing persistence poison. Successful observations
that record conservative `uncertain` state retain that fail-closed behavior: the
change does not silently clear route faults, pending work, budgets or ownership.
The original `from_bytes` / `_from_data` composed loaders remain unchanged.

## Limits and recovery

This is not a filesystem lease, compare-and-swap, or permission for concurrent
writers. Exclusive ownership of the controller/checkpoint remains required. The
final comparison and ordinary atomic replacement are not an atomic cross-process
operation; a non-cooperating writer after that comparison is outside this guard.
It does not make a later external edit safe, authorize new native actions, or
qualify engine restart/transport flow. Existing operator-owned reconciliation and
the unchanged campaign identity, treatment and original cutoff still apply.

## Regression coverage

`tests/test_solid_resume_observation_transaction.py` reproduces post-snapshot file
replacement/deletion, provisional saves followed by exceptions (including an
interrupt), retained pending state, final flush failure, reentrant reads, action
admission and ordinary subsequent save barriers. It also executes the actual input,
background, outpost and successor controller compositions. All use deterministic
backend objects with actual temporary Linux filesystem operations, not Factorio.
Run this file, the existing review/solid integration tests, and the full suite on
the final candidate. Fixture results are not paid-flow or native crash acceptance.
