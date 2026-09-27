# Experimental paid coal-source bundles

Refs #101, #99, #100 and #103. This is a source implementation and deterministic
regression suite, **not native acceptance, production enablement, or completion
of #101/#92**. The production CLI, supervisor and original campaign treatment are
unchanged. Do not deploy this extension into a retained campaign merely because
its tests pass.

## Supported topology

The explicit `CoalSupplyFactory` / `coal_loop_type(solid_loop_type(Base))`
composition supports two to four distinct owned fuel consumers. Each gets its own
paid normal-quality electric mining drill, paid wooden chest, and paid straight
solid corridor with electric inserters. Source areas and endpoint identities must
be disjoint. All consumers must have qualified sites before the first payment.

This deliberately does not claim splitter fairness or shared-source distribution:
separate branches cannot drain one another's reserved finite coal area. The
planner offers the next paid increment for the least-advanced eligible branch,
not a repeatedly rebuilt completed branch. Ordinary useful production candidates
are retained and compiled once. Exhausted source project IDs remain tied to the
consumer role and component rather than changing with layout, receipt, or tick.

The existing solid-route bounds still apply: at most four routes in total, straight
cardinal corridors, and at most 24 belts per corridor. The immutable intent list
must include every configured coal consumer's exact chest-to-fuel route. Remaining
slots may hold disjoint downstream **input** routes: two coal consumers plus one
or two downstream routes, or three coal consumers plus one downstream route. Four
coal consumers leave no downstream slot. The list may use any fixed order, but
resume must retain that exact order and all endpoint/item bindings. Additional
fuel consumers, shared endpoints, and invented `coal:` roles are rejected.

Mixed configuration is explicit source-level composition, not a new CLI flag or
permission to modify a running treatment. It does not introduce splitters,
shared-source fan-out or autonomous investment payback for coal mining.
A separate, default-disabled `coal_kit_policy` can fund a configured bundle from
current owned stock; see [paid coal-kit funding](COAL_KIT_FUNDING.md). The separately qualified downstream funding policy is retained. Existing downstream science-policy admission remains opt-in.

## Preconditions, costs and bootstrap

Every accepted source component is placed through the existing paid actor action.
The planner accounts for both sources and all remaining corridors as one network
kit; native preparation rechecks the complete remaining kit and an empty player
craft queue. Once the bundle is committed, the controller preserves its kit against
unrelated spending while honoring other action/background/ownership locks.

Mixed projects share remaining-kit accounting on both sides of the Python/Lua
boundary. An existing downstream paid prefix retains its entire unpaid remainder
when coal starts. A committed coal bundle retains its source parts and receiving
corridors when downstream construction starts, including corridors not yet visible
as a solid commitment. The selected downstream project's own remainder is counted
once, not also subtracted as another project's reservation. An unpaid optional
proposal does not reserve inventory. Pending coal preparation blocks unrelated
solid placement; no simultaneous actor or RCON operations are introduced.

The default configuration requires the kit to be already carried. The explicit
`coal_kit_policy=True` source-level composition can acquire or handcraft a missing
kit from currently owned stock under the bounded funding contract. Neither mode
constructs a power trunk, establishes burner self-fueling, or estimates economic
payback from measured coal demand and avoided trips. Those are remaining policy
and integration work, not claims supplied by the fixtures.

Each drill and inserter requires observed coverage by an owned energized electric
network. A powered owned machine is a witness, not proof that the power system will
sustain all added load. Electric drills use no coal directly, but this does not
make their power/bootstrap free. Insufficient power, missing kit, blocked sites,
unsupported prototypes and overlapping mining areas reject qualification without
placement. A negative survey is cached for at most 600 game ticks; permissions
always use fresh preparation checks.

## Durable construction and recovery

`factory_coal_build` accepts exactly `target`, `layout`, `part` and `receipt`.
The supported parts are `chest` and `drill`. Chests precede their receiving paid
corridors; drills are placed last, after the path is ready and its buffers are
empty. Construction never inserts seed coal or spawns construction components.

Before actor placement, the native journal records the exact prepared geometry
and receipt, then marks dispatching before calling the paid actor action. An exact
returned placement, one-item payment delta and owned native identity are required
to register a placed part. An ambiguous dispatch cannot be adopted, repaid or
replayed automatically. A retained exact prepared action permits one checkpointed
replay under the existing pending attempt identity. Lost acknowledgement after a
verified receipt reconciles without a second payment.

The checkpoint binds the explicit target list, actor/surface/force epoch, frozen
whole-bundle geometry and monotonically paid source prefixes. Normal solid-route
commitments remain separate and are cross-checked. A new source part can only be
adopted against its attached prepared/pending plan and exact receipt. A missing or
replaced source, conflicting receipt, lost bundle consumer, alias or changed epoch
makes the controller uncertain rather than resetting ownership or budgets.

Coal validation runs inside #121's complete first-resume observation transaction,
via `_observe_solid`, not outside its final checkpoint publication. Replacement,
deletion, validation failure and interruption preserve the retained file or other
writer's bytes and poison further action/save admission. A successful first resumed
observation flushes once after all validators. Subsequent authoritative saves remain
synchronous. This is not an atomic filesystem lease; exclusive checkpoint ownership
is still required, as documented in `SOLID_INITIAL_OBSERVATION_TRANSACTION.md`.

The combined native solid extension uses implementation revision 4 and retains
the full-corridor reservation marker. Coal uses implementation revision 4. These
revisions include the quality-aware power API and reconciled mixed ownership
guards. Earlier installed revisions fail closed; retained paid state is preserved.
No in-place runtime migration or
campaign treatment change is authorized by this source.

## Observation and attribution

The Lua extension participates in the existing single ordered solid observation.
The Python adapter does not issue a second observation or parallelize the native
client. It normalizes only empty map encodings; strict Python validation rejects
malformed schema, stale ticks, mismatched epochs and missing source/route evidence.

The experimental delivery counter is named
`exclusive_mined_coal_lower_bound`. It subtracts coal held in the source chest,
route belts and arms, and a one-item internal-drill uncertainty from finite reserved
resource depletion. Delivery estimates are monotonic lower bounds. It does not
use a mining drill's `products_finished` as a mining counter. Native checks reject
variable yields, required fluids, infinite resources, productivity, modules,
nonstandard depletion ratios, full-stack drill output and intersecting other drills.
Ordinary fair-action mining/placement paths protect reserved resources/footprints.

Manual coal insertion uses a separate write-ahead receipt journal. It is included
in the consumer energy balance but never credited as automatic transport. Fuel
inventory, remaining burning fuel and stored burner heat are accounted separately;
turning an item into burner heat does not count as consumed energy. A missing or
ambiguous manual receipt rejects flow attribution. Same-tick journaled insertions
are reconciled without treating them as unexplained inventory gains.

The bounded observation predicate requires every configured consumer's current
identities, paid path and matching source evidence, at least three positive
observations over at least 120 ticks, at least three conservatively delivered items,
positive energy consumption and recent positive delivery. This predicate is **not**
the useful 30-minute science/research acceptance gate. Power loss suppresses the
productive predicate without erasing the paid topology; backpressure and resource
depletion do not trigger rebuilding. Manual fuel recovery remains separately
journaled where its fresh preconditions hold.

### Unqualified engine assumptions

The fixtures model resource depletion, transport and consumption. They do not run
Factorio physics. The exact deployed engine/FLE/mod/quality versions have not been
read back here. The proposed mining area/drop geometry, finite resource exclusivity,
one-item drill-buffer bound, actual inventories, burner heat semantics and power
coverage must be independently checked on that exact native configuration before
using these lower bounds as evidence. Unsupported shapes fail closed; this does
not prove that every real configuration has been rejected correctly. Operator/mod
mutations outside the bound actor are not made safe by these counters.

## Tests

Run the declared repository test dependencies and commands from a real checkout:

```sh
python -m pip install -e '.[test]'
PYTHONPATH=src python -m pytest tests/test_coal_supply_reproduction.py \
  tests/test_coal_supply_contract.py tests/test_coal_supply_lua.py \
  tests/test_coal_supply_integration.py tests/test_coal_resume_transaction.py
PYTHONPATH=src python -m pytest tests/test_solid_resume_observation_transaction.py
PYTHONPATH=src python -m pytest tests/
```

The suite exercises real Lua 5.2 extension code with modeled Factorio API shapes;
Python tests exercise actual command validation, candidate composition, checkpoints
and temporary filesystem operations. Two-, three- and four-consumer examples,
paid replay/ack loss, ambiguous payment, whole-kit admission, manual attribution,
power loss, backpressure, depletion, unsupported prototypes, ownership changes,
current observation binding and post-coal resume publication are covered. Full
browser testing requires the separately declared browser dependencies/binary.
No fixture or test count is an independent final-source review.

## Remaining delivery and acceptance gates

Preserve #101/#92/#103 as open. Complete the missing demand/lead-time and kit/payback
policy before claiming full source scope. Independently review and natively qualify
the bounded mixed-logistics composition; its fixture pass is not that qualification.
Obtain independent final-source review, configured existing-identity SSH signing,
exact-head hosted checks and normal PR merge; retain separate post-merge readback.
Then use the authorized native owner/session, without changing identity or cutoff,
for isolated paid construction, real mining/transport, manual recovery and restart
qualification. Collect actual coal savings, burner demand, power load, downstream
material flow, sustained science consumption/research and timing/resource outcomes.
The original authorized integrated window must contain at least the specified
30 minutes. A shorter probe, source merge or new campaign does not substitute.


## Mixed-project regression boundary

`tests/test_coal_mixed_transport.py` executes the real Python composition and the
Lua 5.2 extensions against modeled Factorio APIs. It covers both maximum mixed
layouts (2+2 and 3+1), exact-kit positive controls and one-short refusals, both
reservation directions, malformed/overlapping intents, immutable treatment
binding, acknowledgment loss, ready-science preservation, and the complete
first-resume publication barrier under replacement, deletion and interruption.
The modeled transport pulse is not native physics or measured science throughput.

`tests/test_coal_private_supply.py` preserves an existing observation boundary:
the small observer fixture omits container `output`; actual native containers
can expose it. The explicit private-source filter ensures general manual-coal planning can still offer a separate fair gather
rather than an unauthorized network-buffer pickup. These cases add coverage of
existing behavior; they are not six newly repaired defects.

## General-planner isolation (coordinated continuation)

Paid source chests and drills belong to the dedicated network. Their output is
not a general collectible supply forecast and cannot be offered for player
pickup. The shared ledger, ordinary pickup selection, and ready-work batch-wait
fallback now use the same snapshot-bound source-role filter. It excludes the
reserved roles and any aliases of their paid native identities. The controller
still rejects aliased ownership; the filter does not adopt an alias or authorize
its use. Invalid or stale coal-extension data raises rather than reverting to an
unrestricted legacy inventory view.

The regression combines source Lua API-shaped doubles with the ordinary
`FactoryPlanner` and `ReadyWorkPlanner`. The small shared Lua observer fixture
omits container output; the regression supplies the field from the same modeled
chest inventory, matching the `lua/factory.lua` container observation contract.
Without this field the earlier source tests could not expose the interaction.
With 20 coal in a protected chest and none carried, the previous general planner
selected a forbidden extraction and omitted the eight-coal gather. The corrected
planner offers ordinary bounded acquisition instead. Unrelated owned outputs and
carried unreserved coal keep their existing accounting.

A composed controller fixture pays for both networks incrementally, retains the
source and route commitments, then services the independent eight-coal request.
Both a normal reply and modeled lost reply preserve exactly one gather and leave
the chest's 20 coal untouched. These are source regressions, not Factorio mining,
physics, actual provider execution, measured hauling savings, or native acceptance.
No production feature, immutable treatment, checkpoint schema, receipt, failure
identity, or native permission is changed by this private-stock correction. The
separate coal-kit funding extension is documented in COAL_KIT_FUNDING.md; coal
demand/payback investment and native qualification remain outstanding.


## Current-main reconciliation

The combined source selects the `614aeba7` coal foundation and `e80b356f` mixed
native/ownership implementation. It incorporates `9f540bab` private-source
isolation and the independent `b8bf1585` downstream-project admission correction
and regression coverage. The latter packet's older native reservation code is
not substituted for the selected implementation. The complete explicit intent
list may use any fixed order; checkpoint/native reattachment retains that order.
Extra routes are non-coal input routes and remain disjoint, within four total.

Current-main paid downstream kit funding and queued-recipe guards remain active.
A paid coal corridor cannot suppress fresh downstream kit acquisition, but any
pending corridor mutation still blocks it. All kit acquisition paths exclude
private coal-source output and aliases. Construction preserves both families'
whole unpaid kit and all future corridor/source footprints, including unpaid
coal corridors after the first bundle commitment. A source bundle with adjacent
future coal corridors is rejected before payment.

Manual-fuel attribution rejects preexisting receipts before creating a journal
or dispatching, and accepts only newly recorded receipts within the action's tick
interval. Ambiguous manual transfers and coal faults block downstream native
construction as well as coal work. Lost replies with a newly retained receipt
still reconcile once through the existing machinery.

Every native mixed construction preparation and final build revalidates the
entire committed coal bundle, including paid source and receiving-corridor
identity/geometry, resource geometry and owned power. An unrelated downstream
corridor cannot rely on the previous observation while a source or receiving
component is deleted or replaced during approach. Any other mixed corridor's
pending journal or fault blocks payment; only the selected command's exact
prepared journal may proceed. Healthy partial bundles remain supported.
Coal revision 3 cannot be reattached under
revision 4; its retained state requires reconciliation rather than silent reuse.

Power checks use `LuaEntityPrototype.get_supply_area_distance(pole.quality)`:
https://lua-api.factorio.com/2.0.72/classes/LuaEntityPrototype.html#get_supply_area_distance
The pinned 2.0.72 runtime API declares this method, not the old runtime attribute.
Strict API-shaped fixture checks are not qualification of the installed engine.
