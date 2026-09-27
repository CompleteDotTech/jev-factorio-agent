# Coal funding, native ownership and checkpoint composition

Refs #95, #100, #101 and #103. These are source and local Linux checks, not
Factorio-engine qualification, deployment approval or #92 acceptance.

The reviewed-in-isolation predecessor units have different responsibilities:

- Bounded coal-kit funding acquires actual paid components and preserves their
  provisional holds, stable budgets and immutable configuration.
- Native pending-owner binding rejects a source/corridor journal without the
  controller's exact durable plan, step, attempt, receipt and pending identity.
- Install-bound checkpoint publication rejects a detected conflicting file and
  stops the existing controller before another mutation; parent-directory and
  primary-exception protections remain part of that writer.

The integration preserves all three; a whole-writer replacement or a coal mixin
replacement from a predecessor is not an acceptable reconciliation.

## Fresh cross-interface regressions

`tests/test_coal_checkpoint_owner_composition.py` adds 24 cases:

1. After a paid kit pickup, an unowned source journal must not become the next
   funding/construction action, both before and after a paid source prefix. The
   checks cover prepared, dispatching and placed journals, in-process and on
   checkpoint resume. Provisional holds, paid prefixes and existing failure
   history are retained. A later disappearance of the journal does not clear the
   uncertain-state barrier.
2. An exact owned first-source prepare or lost post-payment acknowledgement must
   still recover, both in-process and on resume. The attempt identity is retained;
   a prepared replay uses the same parameters, while a placed component is not
   paid for again. A lost reply alone is not a new observation: provisional holds
   transfer only after fresh bundle/ownership reconciliation.
3. A conflicting checkpoint installation during paid kit extraction or crafting,
   at either prepared or returned state and either the rename or directory-sync
   boundary, must fail the save. The conflicting file, original pending/attempt,
   funding state and prior payment count remain unchanged by subsequent calls to
   that failed controller. A fresh controller is not automatically authorized to
   overwrite or adopt conflicting on-disk state; owner reconciliation is required.

The new cases produced **14 failures and 10 passing controls** on the exact
coal-kit-only candidate `e60a924301e4b8b477c383cac6d73987a852b02b` on base
`fa8c29ef1075af976ee529777b85cf21047a489d`. They all pass with the combined source.
The six extra native-owner failures occur after the paid source prefix, and eight
installation failures occur around kit payment. The already-closed uncommitted
orphan cases and four legitimate recovery cases are passing controls, not newly
fixed defects. Test totals from separate source variants must not be added as if
they were a single executed suite.

## Exact predecessor identities

All identities below are Git **trees**, not newly signed commits:

| Unit | Tree | Full-index patch SHA-256 |
|---|---|---|
| Reconciled checkpoint | `9d9132c46acc0c26105537db20b6b7f69f044b74` | `346d6b7e34dfd686c64ba303d85e67a7e9500131b9f02ddfdb552c00d229c066` |
| Native pending owner | `bc5663d913047bea70d0fd7b15fbba92b510f87c` | `c15779e2c95d1bd1d9687b7396872d446a11bcef7d59a584478536eaa6899c50` |
| Bounded coal-kit funding | `e60a924301e4b8b477c383cac6d73987a852b02b` | `20763e77641bb572399611ccd6f37c6b4b11e2c0db2c8bb121f5aa602301aec2` |

Their exact three-lane composition before adding this document and the new tests
has tree `037fb7d082c77d1b56294d2088a53be90be587e4`. It passed 4,496 tests with 77
skips in one executed Linux/Python 3.13.5 run. This is not an independent approval
or a hosted check of a signed source head. Final packet manifests identify the
combined final tree and exact executed suite, rather than embedding a recursive
self-hash in this document.

## Publication and native gates

Keep the active funding-evidence PR #129 separate until its publisher's final
source, reviews and checks are read back. It is not included in this comparison.
Reconcile any advanced main in a clean owned worktree, rerun combined tests,
obtain independent exact-source review, sign with the existing configured SSH
identity, push a focused PR, pass hosted checks and merge through the normal path.
Do not treat these blobs/trees or this coordinator's tests as that lifecycle.

Autonomous demand/payback selection, production activation and installed-engine
qualification remain coal-source obligations. The explicit-target coal kit policy
still defaults off and reports unknown hauling/net-return values as unknown. This
integration does not change that policy or supply a missing native measurement.
No campaign, source/configuration treatment, cutoff, save, receipt history,
ownership, failure budget or host allocation is changed by these tests. The
original authorized >=30-minute useful-progress window, matched native performance
and resource comparisons, paid material flows and engine recovery remain required.

## Current-main ownership finalization

Integration retains PR129's funding evidence interfaces, PR130's corrected
checkpoint writer, and PR131's exact pending-owner checks. Fresh cross-tests
reproduced premature release of expired funding under persisted uncertainty,
including after resume, and inner downstream release before the outer coal
ownership check. Downstream finalization now waits for coal validation on the
same observation; both funding families preserve holds and failure counts while
ownership is uncertain. Valid expiry still releases exactly once without a paid
action. This adds no backend read or native command.
