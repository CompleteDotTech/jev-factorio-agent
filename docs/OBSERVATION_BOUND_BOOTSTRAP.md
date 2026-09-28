# Bound bootstrap drill after travel

The coherent observer's actor-radius scan is bounded to 1,000 tiles and 129
results. A previously verified bootstrap drill can fall outside that scan as
the actor travels. The controller now sends the drill's last validated position
with its unit number. When the local scan misses it, the observer reads only
that exact site and requires the same unit, position, force and surface before
reporting its fuel or output chest. A missing or replaced drill still fails
closed. Initial bootstrap discovery keeps the bounded actor-radius scan.

This changes `observation_v2.lua` bytes. An installed-source manifest for the
previous observer, including the retained e759 migration profile, must not be
silently treated as this revision. Coordinate a separate reviewed migration
and native acceptance before enabling this source for a retained session. The
synthetic Lua and Python tests show the source contract, not native-game speed
or a live reattachment result.

The old reviewed observer SHA-256 is
`30cce48ab896579473d625d38daea86dc7c61710092255436974d0111b41b416`;
this source has SHA-256
`f51ea4aeb66b5c11366dbfe37cb755f2187152fa634928ac8a911f670d746780`.
