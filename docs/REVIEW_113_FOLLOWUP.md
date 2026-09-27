# PR #113 review follow-up: observation and logical timing

References: #94, #96, #103. This source follow-up addresses four independent
post-merge review findings on `b2edc7aae798d189a331f32d01d2342f3fb1e544`.
It does not close native acceptance #92 or claim a new deployment.

## Corrected behavior

`native_io` now accepts an optional response-status validator inside the measured
logical operation. A returned `Cannot execute command.` rejection increments both
logical failure and phase failure once, including through nested session/RCON
wrappers. Its known UTF-8 response bytes remain countable. A transport exception
still has unknown response size. Timing disabled/nested paths still validate;
original exceptions and ordinary return values are preserved. No exception text,
Lua script, credential or native identifier is added to the timing summary. These
are logical calls/content bytes, not packet counts or pure network measurements.

All explicit native response decodes in paid input/output/outpost/solid/launch
adapters, interaction-reach confirmations, corridor-origin reads and the legacy
FLE player-state read now enter `native_decode`. The already-instrumented
`ObservationProfile.decode` is not wrapped again. Filesystem game-state parsing
in `play_api` is not relabeled as RCON. Opaque FLE-internal decode/retry boundaries
remain unavailable; no guessed decomposition is substituted.

After choosing the pinned bootstrap drill, the observer searches only its drop
position for a same-force wooden chest. A .15-radius, two-result bounded query
preserves the existing +/- .1 center tolerance and detects ambiguity. It does
not enlarge the actor-centered 1,000-tile search or relax the 128-entity combined
payload budget. Chest identity, surface, force and inventory are checked fresh.
A chest already seen is not counted twice; an additional chest that exceeds the
combined budget fails closed. No entity is built, adopted or repaired by this read.
The additional query is inside the existing single ordered native command, not
an additional RCON call or evidence of tick-atomic support on unqualified runtimes.

The report includes the unrepresented prefix `first_iteration_index - 1` in its
existing missing-publication count. Its scope now explicitly says **missing from
the analyzed input**, including a sliced stream's prefix, not proof of runtime
record loss. Duplicate/regressing timing indices remain invalid; legacy rows do
not fabricate timing evidence, and the unobservable final tail remains unknown.

## Tests and evidence boundary

`tests/test_review_113_regressions.py` uses actual adapters, timing wrappers, the
actual observer Lua under Lua 5.2, and the Python decoder with deterministic
transport/engine doubles. The first 36 cases reproduced 23 failures and 13
passing controls before source changes. Additional negative and boundary cases
cover malformed replies, transport exceptions, disabled timing, nested wrappers,
full observation budgets, replaced/foreign endpoints and missing timing indices.
All runtime/throughput claims still require authorized native qualification.

The four original review threads are not resolved merely by this local patch.
Independent review of the final source, configured SSH-signed publication,
exact-head hosted checks and normal merging remain required.

## API reference

Factorio's official runtime API documents that position-plus-radius entity
searches test entity centers, and that name/force/area filters are conjunctive:
<https://lua-api.factorio.com/latest/classes/LuaSurface.html#find_entities_filtered>.
The documentation is external API context, not a test of the deployed engine.
