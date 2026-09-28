This fixture preserves the JSON wire structure of an isolated base-only Factorio
2.0.77 observation captured before paid gameplay. Session, player/character,
force/surface identities, coordinates, ticks, site anchors and profiler durations
are synthetic. In particular, empty Lua sequences retain their observed `{}`
encoding. It is an offline decoder regression, not native performance evidence.

The unmodified response remains in the private execution handoff, SHA256
`89f92339beabd45237bcc07535a46861eda7de1c8fb14be7181e429eb85a1d33`.
