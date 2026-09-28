# Prepared coal economics arithmetic contract

`planning/coal_economic_proof.py` and `planning/coal_economics.py` prepare a pure,
strict arithmetic contract for issue #101. They are not connected to the native
observer, production admission or mutation dispatch. A successful forecast never
claims native payback and always carries `mutation_authorized=false`.

The first supported model is a bounded, normal-quality, base-only Factorio 2.0.77
steam network with two to four distinct coal consumers. One consumer is its owned
boiler. The complete coal kit contains one electric drill/chest and two inserters
per branch, plus one to twenty-four straight belts per branch. The existing native
route contract remains responsible for geometry and ownership qualification.

## Inputs and qualification boundary

`EconomicInput.parse` produces frozen typed data. Inputs have exact versioned
keys, bounded integer quantities, unique native identities, complete kit counts,
and epoch/bundle/catalog/topology/prototype evidence digests. Manual measurements
carry the exact target role/unit and delivered quantity for every consumer;
aggregate coal alone is insufficient to price a grouped service trip.

These digests identify evidence; their presence does **not** authenticate it.
The future native decoder must independently qualify the complete owned power and
fluid graph, prototype values, receipt attribution, actor epoch and exact bundle.
A copied digest or a caller-supplied dictionary is not action authority.
`validate_proof` recomputes the full canonical input and arithmetic, including
strict JSON types, but cannot establish the external provenance of those inputs.

The exact 2.0.77 prototype query has returned finite raw values. Electrical unit
calibration, complete topology/buffer qualification and current-owned receipt
binding remain separate native work. Before constructing this arithmetic input:

- Round electric consumption/drain and buffer-cost bounds upward to whole joules.
- Round generation capacity, mining speed and conversion efficiency downward.
- Express rate inputs in joules per game tick only after the exact engine's units
  are qualified; mining speed and efficiencies use integer millionths.
- Derive buffer capacity from the complete supported steam/electric graph. Do not
  turn a missing buffer or unavailable measurement into zero.
- Derive full acquisition time from the paid-bill solver and material reservations.
  Zero acquisition time is allowed only when its bound bill evidence proves the
  kit is already carried; placement and material cost remain.
- Derive required coal demand from bounded current recipe work or project a
  receipt-attributed observed burn sample, retaining the explicit forecast basis.
  Inventory depletion alone does not qualify observed burn. Account current fuel
  stock; the evaluator also rejects demand exceeding a native maximum burn bound.
- Bind manual-cycle receipts to all exact target identities. Same-bundle identity
  does not make delivery to one target evidence of a trip to another.

All consumer burn and steam conversions require the supported normal-quality
base prototypes and their qualified semantics. Do not apply this model to modded
fuels, mixed quality, foreign generators, overlap, unobserved storage or unknown
energy loss. Unsupported evidence remains deferred by the production gate.

## Calculations and units

The fixed `owned-steam-coal-v1` policy uses a horizon of 600–216,000 game ticks.
The explicit `startup_elapsed_game_ticks_forecast` reduces the source's active
forecast window and prices bootstrap fuel. It must be at least the sum of serial
acquisition, placement and walking actor-time estimates. API waits and crafting
gaps can increase elapsed game time without increasing active actor work. The
declared elapsed estimate is not a guaranteed scheduling upper bound; production
integration still needs native deadline and fresh fuel-reserve enforcement.
Source capacity uses native mining speed/time, a declared 75% utilization
forecast and finite ore as a ceiling. Neither value promises actual future flow.

Operating coal is rounded upward from the complete existing and proposed load
maximum over the whole horizon, plus buffer capacity, divided by qualified
boiler/generator efficiency and coal fuel value. Full-horizon charging intentionally
overestimates partial construction and idle time. Generation capacity must cover
that maximum. Bootstrap fuel must cover elapsed startup's projected consumption and
buffer charge. This is deliberately conservative and may reject investments that
a later, independently qualified workload model could justify.

The boiler branch covers internal network coal first. That coal is excluded from
net useful external return. Newly added electrical consumption never becomes an
invented baseline hauling benefit. Avoided manual cycles are the **minimum** of
each target's forecast demand divided by that target's measured delivery per
cycle, rounded down. No fractional trip or irrelevant-target allocation receives
credit. Avoided actor ticks must exceed priced acquisition, placement, walking and
recurring service ticks by a strict 5/4 margin.

Material opportunity is a separate points budget: drill 20, chest 2, inserter 5,
belt 1, at most 200 points. These are fixed policy valuations, not native item
prices or time. Points, coal and joules are never added to actor ticks. A carried
kit still incurs this budget and paid placement time.

## Integration still required

Bind canonical economic proof to the offered plan and versioned funding checkpoint,
distinguishing admitted-but-unpaid work from first-payment pending and paid
continuation. Rebuild the decision from fresh native facts and atomically recheck
its native identity/bounds predicate immediately before extract/craft/build
consumption, including after movement. Preserve ambiguous receipts and genuine
paid continuation. The current always-negative production gate remains unchanged.

Synthetic tests demonstrate positive/negative arithmetic, asymmetric service
allocation, capacity/stock/burn limits, strict types, proof tampering and compatible
unit comparisons. Inter-call delay tests hold actor cost fixed while reducing the
active forecast window and increasing bootstrap fuel. They do not demonstrate
actual engine economics, native
selection, paid first-payment proof, campaign acceptance or deployment.
