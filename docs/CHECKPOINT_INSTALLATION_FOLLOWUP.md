# Checkpoint installation and directory durability — #95 / #96

The common writer retains exact-state coalescing and every changed authoritative
write-ahead barrier. The already-merged controller and audit counter fail-stop
guards are unchanged. No checkpoint schema, ownership, receipt, failure budget,
source treatment or campaign cutoff changes.

## Installation boundary

A save previously cached whichever pathname metadata it found after directory
synchronization, even when another entry or different bytes had replaced the
flushed payload. On POSIX the corrected writer holds the original file descriptor
through replacement, validates the installed entry against that descriptor, pins
the full installed metadata across directory fsync, then reads its bytes once.
On Windows it closes the temporary file before replacement to preserve ordinary
sharing semantics, reopens the installed entry, and compares its descriptor and
bytes to the captured flushed identity. That branch has modeled sharing tests,
not an actual Windows run; it retains the existing lack of POSIX directory fsync. The
read catches a same-inode/same-size edit with restored mtime during replacement;
metadata alone cannot distinguish that edit from rename's legitimate ctime change.
A conflicting installation fails without caching success, retrying over the
conflicting file, or deleting it. Cleanup errors cannot mask a primary save
failure. Existing controller guards stop subsequent observation or actuation.

The read is bounded to the serialized payload length plus one byte and is
additional, necessary safety work: this correction does not claim a speedup.
Exact repeated state still avoids capture, serialization, reading and fsync.
The contract assumes one authorized writer. It is not an interprocess ownership
lock, authenticity check, malicious-writer defense, arbitrary-signal guarantee,
or proof against a change and restoration invisible at every check boundary.

## Parent directories

Missing directory levels are created one at a time; each new entry's containing
directory is fsynced before any authoritative checkpoint file is prepared.
Existing durably provisioned directories retain one file plus one directory sync
per changed save. N missing levels add N directory fsync calls. Provisioning
failure leaves created directories for inspection and poisons the owning
controller through its existing guard. Do not resume using those residual
folders merely because they now exist: the established owner must durably
provision/reconcile them before constructing a replacement controller. Existing
parents remain an operator-provisioned precondition, not something stat proves.

## Accounting and compatibility

`directory_sync_ns` includes both installation and new-parent synchronization.
`parent_directory_sync_calls` is a subset of `directory_sync_calls`; do not add
the two as independent work. Sync call counts include attempted calls.
`verification_read_calls` and `verification_read_bytes` report actual verification
reads. `installation_check_ns` is nested in inclusive checkpoint timing; it is
not additive to the full checkpoint/controller elapsed time. The iteration ledger
tracks installation verification with wall, process CPU and thread CPU clocks.

The performance reader recognizes these additional bounded operation keys.
`io_measured_calls` distinguishes instrumented requests from old records; missing
old fields are not retroactively treated as measured zero. Use the matching
reader for new records; older strict telemetry readers may reject new keys.
Authoritative checkpoint serialization remains byte-compatible.

## Qualification and rollback

The regression suite uses real local files and explicit fault injection, plus
real controller classes with a deterministic backend. It covers replacement,
deletion, symlinks, truncation, same-size edits, partial writes, parent creation,
parent replacement, sync failure, cleanup failure and prepared/returned state.
These checks are not Factorio-engine recovery, hardware power-loss tests,
production deployment or #92 acceptance. Preserve native campaign state and use
the established authorized rollout/recovery window for those claims.

Before publication, reconcile the older seven-file checkpoint handoff rather than
stacking it: this delta deliberately does not duplicate its already-merged
controller counter guard. An exact-final-source independent review and the
existing SSH signer are required. Roll back published source through a reviewed
revert, not checkpoint replacement or failure-history reset. An unmerged patch
requires no operational rollback.

Windows sharing reference: https://docs.python.org/3.13/library/tempfile.html
(open handles that do not share delete access must close before deletion).

## Reconciliation with the concurrently published installation candidate

The separate three-file candidate recorded on #95 uses source blob
`a923f3625ff4e8a361337fec2fea3fb37057257f` and tree
`430309133c86b62e7ad1f35d2753946667371c81`. Its directory-descriptor
cleanup correction is incorporated here: a close failure or interruption cannot
replace an existing fsync failure/interruption; a lone close failure still fails
the save. Four fresh before-change failures and a success control exercise this
boundary. The stronger byte verification, parent identity checks and explicit
read/sync accounting in this continuation are retained. Do not stack the entire
alternative writer over this delta. This reconciliation is not independent review
or native acceptance.

## Exact-source reconciliation of the installation alternatives

The metadata-only four-file tree `0a9a82075a6d0284869d9173bfcb279281ef22ef`
and the six-file byte-verifying tree
`984980b69946560dc29ff66e57fdfb7f8c4adb46` were compared on the same merged #128
base. Neither entire writer is stacked over the other. The byte verification,
parent identity checks, bounded read/sync accounting, and separate timing-test
correction from the six-file candidate are retained. The metadata-only proposal
is historical, not an alternative deployment recommendation.

Cross-testing confirmed that metadata alone misses a short write and a same-size
modification with restored mtime before the first installation observation. The
byte-verifying proposal, in turn, discarded substituted temporary entries and
could conceal primary storage errors with secondary diagnostic failures. Those
boundaries are now reconciled. A regular-file temporary identity is captured
before writing; cleanup removes only that entry. Unknown or substituted entries
are preserved for explicit owner reconciliation. Concurrent creation while
provisioning a missing directory chain fails closed rather than adopting another
owner's entry.

File and directory handles are closed on error without replacing the original
storage exception or interruption. A failed `fdopen` closes its still-owned raw
descriptor after rechecking identity; a released or detectably reused descriptor
is not closed again. This check does not establish cross-thread atomic ownership
or protect an unobserved same-identity ABA. A lone close or timing failure remains a failed save and clears the
cache, including failures before the first timing sample and after installation.
Elapsed-time failure handling does not invent a duration or hide a primary
exception. None of these diagnostics authorize a subsequent mutation.

The additional `test_checkpoint_installation_guard.py`,
`test_checkpoint_parent_provisioning.py`, and
`test_checkpoint_primary_failures.py` tests include the real Python mixed
coal/downstream controller with deterministic backend doubles, prepared/returned
barriers, paid-prefix preservation, substituted temporary evidence, and nested
storage/clock/close/interruption failures. The exception contract accepts
`OSError` for installation failure while retaining the existing controller's
`RuntimeError` fail-stop behavior. Functional checks were not removed to satisfy
an exception-class mismatch.

The non-POSIX branch closes the temporary handle before replacement and checks
an explicitly reopened read handle. Its tests model Windows sharing; they do not
qualify an actual Windows filesystem. The installed byte read is bounded by the
serialized payload length plus one. It is not an interprocess lock, proof against
unobserved ABA changes, physical power-loss evidence, or native Factorio recovery.

No schema, paid-kit policy, native transport, campaign identity/cutoff, original
receipts, ownership, or failure budget is changed. Do not combine either old
whole-writer patch with this reconciled delta. Source delivery still requires the
configured SSH signer, independent exact-final-source review, required hosted
checks, a normal permitted merge, and the native acceptance gates in #92.

## Current-main integration

The final integration retains the byte-verifying nine-file candidate
`9d9132c46acc0c26105537db20b6b7f69f044b74` and its primary-error and temporary
identity guards. The parallel `ae37c9e89d5b4fdd03d157f835d2b3150c964f68`
variant contributes the pre-replacement short-write length check, separate
`checkpoint_parent_sync` ledger span, timed final installation check, and fresh
interpreter CPU/wait control. Neither predecessor is applied over the other.
Short writes preserve any previously installed checkpoint. Parent sync durations
remain included in `directory_sync_ns`; ledger spans separately attribute parent
setup and final directory synchronization, without double counting elapsed time.
The final installation timing preserves an existing storage failure if its
diagnostic clock also fails. This integration leaves merged funding-evidence
source and native campaign state unchanged.

Native-Linux fault injection also reproduced same-size edits during directory
sync whose timestamp tuple remained unchanged. Final byte verification therefore
follows directory sync; it is the first read from the retained stream, avoiding
stale buffered readback. This retains one bounded read per successful changed
save while detecting that reproduced metadata collision.
