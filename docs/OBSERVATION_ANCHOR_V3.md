# Bounded oil-anchor candidate for coherent observation

Issue #94. This source candidate is not an installed callback or a native-game
performance result. The retained isolated session currently has the exact
`e759-observation-v2-bound-bootstrap-v2` installation. Its
`observation_v2.lua` source and manifest hash remain unchanged.

## Observed mismatch

At actor position (62.27734375, -27.12890625), the installed coherent
observer's 256-tile crude-oil query returned zero. A read-only 512-tile native
query returned seven oil entities; its nearest positive oil entity was at
(15.5, 383.5), distance 413.284670096 tiles, matching the legacy FLE result.
The planner requires a nearby crude-oil key before it offers a pumpjack and
eventually stops exploring at 32 chunks. A fixed 256-tile actor query can
therefore leave an already generated oil site invisible even at that limit.
The observations do not establish a global nearest oil site or a gameplay
throughput improvement.

`observation_v2_anchor_v3.lua` first searches oil within 256 tiles, then
512 and at most 1024 only if the previous query returned no usable positive
entity. A near depleted oil patch cannot hide a farther live witness. Each query
returns at most 129 entities. A saturated query can still publish a valid,
positive, same-surface oil entity among its returned results; the per-snapshot
diagnostics mark it saturated and explicitly describe the result as a bounded
witness, never a globally nearest site. This avoids permanently suppressing
pumpjack planning in a dense oil field. Ordinary native paid-placement checks
still decide whether a proposed site can actually be used. No world
generation, placement, mining, or resource credit occurs in the observer.
The maximum radius matches the planner's 32-chunk limit but remains
actor-centered, so absence is only absence from these bounded queries.

Water remains a separate 256-tile, 129-result tile query. Its result is a tile
center. The legacy FLE helper returns a tile origin; the observed coherent
center (27.5, 44.5) and legacy origin (27, 44) describe the same tile, not
two water sources. A saturated water query supplies a bounded witness, not a
global nearest-water guarantee. The decoder binds the exact old or expanded
bounds contract to the verified installed profile and applies the smaller
water radius to water.

## Publication boundary

Fresh coherent installations select the new Lua file; the exact installer
records its SHA-256 under the logical `observation_v2` asset. The existing v2
source file remains byte-identical and validates only the original
`e759-observation-v2-bound-bootstrap-v2` profile. The new
`e759-observation-v2-expanded-oil-v3` profile validates only the new bytes.
An unknown or crossed profile/hash fails closed.

`migrate_observation_anchor_v3` is an explicit owner-only v2-to-v3
transaction, never called during normal attachment. It requires exact
checkpoint and original private receipt hashes, the existing private lock,
matching session/actor and old native manifest, idle native controls, and no
unresolved checkpoint work. Its one Lua command replaces only the observer
callback plus its manifest hash/profile/callback pointer; an in-command Lua
failure restores all four old values. A lost response is ambiguous and must
be reconciled by read-only manifest inspection before any further action.
After acknowledgement, the API checks the exact new manifest and unchanged
checkpoint/receipt bytes. The owner must retain its native receipt, review
source/CI and qualify the new callback on the isolated game before reenabling
coherent gameplay. No migration has run as part of these source tests.

Synthetic tests execute both observer files and the strict Python decoder.
They cover the observed oil geometry, depletion, the 1024-tile cap, query
limits, saturated oil and water witnesses, tile center and rejection of forged
larger water/oil bounds. Run:

```sh
PYTHONPATH=src:tests python -m pytest tests/test_atomic_observation.py \
  tests/test_atomic_observation_lua.py \
  tests/test_native_observation_migration.py \
  tests/test_native_observation_anchor_migration.py -q
```
