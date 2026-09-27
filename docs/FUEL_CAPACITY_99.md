# Bounded native fuel-capacity hints — #99 follow-up

References: #99, #94, #103; native acceptance remains #92. This addition is manual
fuel service, **not** coal mining or automatic distribution (#101).

## Observation contract

The negotiated observation-v2 command reads the actor's normal-quality coal
insertable count from the same main-inventory handle used for contents. It also
reads fuel-inventory capacity for at most 16 distinct owned burner-inserter or
burner-mining-drill identities. Roles are sorted; native identity, name, force
and surface must match the just-observed row. Role aliases share one read,
including an unavailable result. Other consumers remain unknown.

`factory.inventory_insertable.coal` and `entity.fuel_insertable.coal` are optional
**advisory basic-inventory hints**, not reservations or permission. The actor
hint now comes only from a top-level `inventory_capacity` reading bound to the
same tick and main inventory; the decoder discards nested wrapper hints and
publishes the validated value with separate identity evidence. Missing or
unsupported getters remain unknown, including after a prior successful read.
A supported actor getter that fails, or a malformed/out-of-range result, rejects
the observation. Known zero is preserved. Consumer hints remain bounded to 16
owned native identities. No extra RCON call, FLE helper, authoritative checkpoint
field, ownership change or cross-tick cache is introduced.

This provider runs only in the already-negotiated observation-v2 path. Legacy
observations without capacity retain bounded unknown-capacity behavior, not a
claim of measured capacity. Actual engine/version compatibility remains a native
gate; latest API documentation alone does not establish 2.0.77 deployment support.

## Manual service

Destination hints cap each consumer's deficit. A known-full optional consumer is
deferred once; a known-full required destination cannot trigger pointless coal
acquisition. Aliased identities with conflicting capacity fail closed. A partially
available destination receives only the bounded quantity. Actor capacity limits
new gathering, not use of already carried and unreserved coal. Quantities are
replanned after each fresh observation and receipt; no failure ID or budget is
reset. A retained gather step is checked again against the latest known actor
headroom before dispatch; an oversized step is rejected for replanning without
changing a pending receipt. The existing native transfer capacity preflight
remains authoritative. The detailed capacity evidence stays out of model facts.

## Qualification and limitations

The read-only Lua/adapter and composed-planner fixtures exercise known zero,
partial/full/unknown capacity, aliases, replacement/force/surface mismatch,
unsupported getters, bad wire values, the 16-consumer budget and sequential
replanning. They do not prove path reachability, native fuel trips, actual burn
rate, free native capacity after walking, or useful science/research throughput.
This source needs independent review, existing-identity SSH-signed publication,
exact-head hosted CI and normal merge before authorized deployment.

Factorio calls `get_insertable_count` a best-guess result, intended mainly for
basic items in basic inventories; some specialized inventory cases are
inaccurate. The policy treats it accordingly:
<https://lua-api.factorio.com/latest/classes/LuaInventory.html#get_insertable_count>.
No native action is authorized from this estimate alone.
