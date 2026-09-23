# Stage 2: mining outposts for existing manual furnaces

`--mining-outposts` is an opt-in extension of the hierarchical FLE ready-work
controller. It addresses the existing-world case where an iron/copper furnace
cannot obtain a supported local drill/belt input route. It constructs at most
one **burner mining drill and wooden chest per ore** at an observed ore patch,
then collects verified ore in batches for the existing material-supply planner.

This is automated mining with **batched manual hauling**, not an automated belt
connection to the furnace. Existing furnaces, output buffers, direct routes,
bootstrap machines, entity identities, and receipts are not moved, replaced,
adopted, or reassigned. Coal gathering remains on the existing path. Additional
furnace capacity and self-bootstrapping assembler investment are not Stage 2.

## Enablement and existing-world boundary

The option requires all of the following existing settings:

```text
--backend fle
--controller hierarchical
--target rocket_launch
--factory-scheduling ready-work
--furnace-output-buffers
--furnace-input-belts
--mining-outposts
```

These are configuration flags, **not a command to initialize a new world**. Keep
the existing session/checkpoint, `--resume`, `--resume-controller`, positive
poll interval, and original campaign deadline when making an authorized
existing-world transition. Optional `--background-work` remains supported.
Never initialize an FLE backend merely to inspect or test this feature.

A pre-extension input-route checkpoint can be read by the new controller only
at an idle, reconciled boundary: running status, no active plan, no pending
mutation, no reservations, and no acknowledged background job. Enabling appends
an explicit capability event to memory. The loader does not overwrite the source
checkpoint. A new `outposts_schema: 1` extension persists outpost commitments
through ordinary atomic checkpoint saves. Old readers reject it rather than
silently discarding ownership. A controller that has not enabled the capability
rejects outpost runtime telemetry.

The supervisor forwards the option on every restart, includes it in repair
instructions, and protects commitments during repair acceptance. **An existing
supervisor's immutable gameplay configuration cannot be edited in place.**
Changing treatment requires an explicitly authorized, stopped, audited handoff
that preserves the original world, session, start time and cutoff; this PR does
not perform or automate such a live handoff. Do not edit its state JSON, extend
a deadline, start a second owner, or clear an ambiguous action to enable this.
Research manifests distinguish enabled outposts as a separate treatment while
remaining able to read older manifests that omit the field.

## Bounded investment and survey

A proposed outpost is considered for a raw-ore shortage of at least ten units,
with an existing corresponding furnace that has produced at least twenty items.
An available direct input route takes precedence. Existing collectible ore is
used before a new investment. Small bootstrap requests keep their old path.

The read-only native survey searches the already generated exploration area
(maximum 32 chunks of radius), inspecting at most 128 observed ore entities and
eight candidate sites, with four drill orientations each. Failed searches have
a 300-tick cooldown. Rejection records a reason; it does not prove that no other
geometrically possible layout exists. The survey does not generate terrain.

Only supported 2-by-2 burner drills, 1-by-1 wooden chests, native output vectors,
unmixed solid iron/copper ore with deterministic unit yield, and zero mining
productivity bonus are admitted. At least 100 observed ore units must remain.
Nearby foreign miners and reserved production footprints are avoided. Exact
native mining-area contents are checked after drill construction. Unsupported
or depleted proposed sites fall back to the existing planner. A paid site that
changes incompatibly requires reconciliation rather than silent relocation.

Both directions of construction arbitration are guarded: a committed outpost
prevents a competing direct-route prepare/build, and a committed direct route
prevents an outpost build. Generic placement and production-site search respect
reserved outpost footprints.

## Construction, commissioning, and service

1. Acquire one bounded kit through the existing paid crafting/gathering paths,
   retaining five coal for initial commissioning. Kit acquisition does not
   recursively invest in another outpost for its own ingredients.
2. Prepare and freeze the surveyed geometry; walk through ordinary native
   movement and reach checks; build the chest before the drill. Every component
   pays one actual inventory item and has its own receipt and native unit.
3. Recheck the site after walking. Observe each component before advancing.
   A lost response never authorizes duplicate placement: retain the pending
   action and resolve it only with its own paid component evidence.
4. Fuel the drill with an ordinary verified transfer. Before commissioning,
   manual insertion into or extraction from the chest is forbidden. Prove that
   the native drill output targets that chest, its mining target belongs to the
   observed patch, and three positive observed chest-growth samples span at
   least 120 ticks. Patch depletion must match at least three received ore units.
5. Only after that proof collect observed chest contents, normally capped at
   fifty units. Never count a kit, placed drill, elapsed time, forecast yield or
   command acknowledgement as produced ore. The existing player transfers carry
   ore to the unchanged furnace.

Initial fueling uses the retained five coal. Subsequent low-fuel service is
bounded to twenty coal and the native item-stack limit. Small pickups remain
permitted for critical shortfalls and a depleted patch's remaining chest tail.
After observed depletion and tail collection, ordinary observed gathering may
resume; the paid outpost remains owned and is not automatically rebuilt.

Only the existing single dispatcher mutates the world. Background craft locks
still prohibit construction and consumption of unverified craft outputs.
Output-buffer and input-route guards remain in force. Checkpoint write failure
poisons further dispatch. A changed actor, surface, force, component unit,
position, direction, topology, proof or retained commitment fails closed.

Native integration uses hooks inside the existing observer and transfer
endpoint, not an additional tick handler or observer-wrapper chain. Reinstalling
the adapter retains its native cells. These runtime receipts do **not** survive
an arbitrary server reset merely because the Python checkpoint exists.

## Validation and performance measurement

`tests/test_mining_outposts.py` executes the real Python planner/controller,
checkpoint loaders, adapter, CLI and supervisor configuration, plus the actual
Lua outpost survey/builder/commissioning functions over synthetic engine
fixtures. Tests cover payment, native reach, after-walk revalidation, lost
acknowledgements, restart, ownership regression, flow conservation, direct-route
arbitration, background locks, resource depletion, and legacy compatibility.

Synthetic ore pulses are test fixtures, not Factorio benchmark measurements.
No live world is started, changed, or deployed by the tests. No throughput
speedup is claimed from passing them.

For native acceptance use isolated copies of the same actual save, not just the
log export. Compare Stage 1 against Stage 1 plus outposts, recording construction
cost/time, first commissioned ore, mined/collected/delivered quantities, player
mining and travel time, producer starvation, science consumption and milestone
latency. Verify actual ore collection and furnace delivery, not just a building
count. Include distant ore, obstructed sites, partial construction, lost RPC
responses, and a restart with paid commitments. Keep game speed, seed, actor,
resource payment, deadline, and all other treatment settings fixed.

## Rollback

Before any committed outpost exists, stop at an idle, reconciled boundary and
use an audited configuration/source transition. Once outpost commitments exist,
retain a controller capable of reading and enforcing them. **Do not simply
remove the flag or downgrade to an old checkpoint reader.** Disabling requires
an explicit ownership-preserving handoff; this PR intentionally supplies no
silent ownership deletion or native receipt reset. Never replay an uncertain
mutation or replace the world to make a rollback appear successful.
