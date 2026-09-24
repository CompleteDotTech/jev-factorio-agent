# Acceptance review remediation (PR #67)

This change corrects four P1 findings from the completed review of PR #66. It is
remediation of the existing native-acceptance gate, not a new gameplay capability
or a completed native validation phase. See [the operating procedure](NATIVE_ACCEPTANCE.md).

| Review concern | Required invariant | Regression evidence |
| --- | --- | --- |
| Different code across trial arms | Both commit and source fingerprint match between baseline and treatment | Internally consistent but different arm revisions fail all pairs; same-code control passes |
| Unchecked preflight query | Reported digest matches the packaged fixed UTF-8 probe | Missing, arbitrary and altered digests fail despite valid outer bundle hashes |
| Stale final successor checkpoint | Final project identities, paid input/output parts/layouts and exact use/qualification agree with observed and logged state | Matching fully qualified flow fixture passes; same-tick checkpoint lacking the extension fails |
| Transient completion | Research/victory persist; milestone tick agrees with final checkpoint | Disappearing/reappearing goals cannot earn a completion time or shorten the horizon; persistent control can |

Final agreement alone is insufficient. An additional history check rejects a
previously observed paid source disappearing or a completed use/qualification
proof being cleared or reassigned during the trial. Normal unplaced acquisition
intent remains valid unfinished work. All checks are read-only; they do not repair
checkpoints, reassign entities, or replay actions to manufacture consistent evidence.

The isolated integration run `35963669602` tested source commit
`1081708909cc0a8528f1a9dfbc5d4c8e13c22a90`: **124 focused tests passed** and
**1,947 tests passed, 77 skipped** with the browser module excluded. Compilation
and whitespace checks passed. These are synthetic/offline results; normal PR CI
and independent review of the final head remain distinct checks.

The subsequent review identified two terminal-state edge cases. Run `35964123697`
passed the expanded focused suite, full non-browser suite, compilation and
whitespace checks after the following corrections:

- A paused project cannot become active/qualified or acquire another source. This
  is checked from the initial checkpoint through logged project history to the
  final checkpoint. Active-to-paused and active-to-qualified remain valid.
- Native research/victory completion must be retained by an observation at a
  strictly later native tick. Repeated copies of the last-tick state do not count.
  A lone terminal sample has no credited completion time and cannot shorten the
  trial horizon. This is a conservative measurement rule, not an instruction to
  restart a stopped controller or alter a save.

No native guest connection, live preflight, session handoff, gameplay trial,
fault injection, recovery soak or production cutover was performed as part of
this remediation. Execution requires an authorized development-guest connection
and a coordinated matching live session/save/checkpoint boundary. A historical
clone or available TCP transport does not establish that boundary.

Re-evaluate any earlier measurement report with the corrected evaluator before
using it in acceptance review. A reported probe digest and checksums establish
consistency, not external authenticity. Existing archives must not be rewritten
to appear compliant. Reports still retain `native_acceptance: not_accepted` and
`deployment_authorized: false`; the native trials and external operational gates
remain mandatory. Production world, inventory, speed, paid assets, pending work,
failure history and feature flags remain unchanged.
