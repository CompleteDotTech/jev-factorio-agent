# Bounded native interaction approaches

A sulfur extraction failed before its transfer RPC because the old interaction
approach always chose a collision-free point east of the target. That point was
inside a shoreline pocket which the native pathfinder could not reach. Native
read-only observations identified alternative collision-free positions north
and west of the same plant, inside the player's normal interaction distance.

`FairActions.approach` now searches at most sixteen nearby candidate positions
around an existing entity, starting from the actor's side. It retains an arrival
margin inside native interaction distance and tries another candidate only after
an explicitly classified native no-route result, or a completed walk followed by
a negative native reach check. Timeouts and other walking failures propagate.
After a completed walk, the target must retain its native unit identity and
`can_reach_entity` must succeed before the caller can transfer or configure it.

Already reachable entities require no walking. The existing generic construction
point behavior when no entity exists is preserved; it does not claim entity reach.
No interaction range, inventory, receipt, pending-attempt, or failure-budget rule
is changed. An existing ambiguous transfer still requires its usual independent
receipt reconciliation; this approach change does not replay it.

Regression tests execute the generated Lua in Lua 5.4 and cover an unreachable
east-side pocket, alternative approaches, native reach rechecks, changed/missing
targets, bounded candidate searches, duplicate candidates, and propagation of
uncertain walking failures. These are offline correctness tests; they do not
prove native route availability at every generated candidate.
