# Crude-oil connection repair

The production attempt `5fd51946a5c0480481609e8c0219ebad` failed before mutation
with `no_connection_route`. Its exact step fingerprint identifies the crude-oil
connection from the pumpjack to the basic-oil refinery. Native read-only queries
confirmed both ports were placeable, but both narrow L-shaped search corridors
were disconnected. A wider native placement survey found a 356-cell route inside
the endpoints' bounding rectangle, within the existing 390-pipe inventory.

The pipe planner now retains the two fast corridor searches, then searches a
complete rectangle with the same eight-cell margin. Each native query remains
limited to 16,384 cells and all discovery is limited to 65,536 cells. Existing
fluid incompatibility checks, inventory preflight, route length limit, normal
walking and per-cell placement remain in force. The observed route requires
20,274 fallback cells, or 33,500 including both original corridor searches.
This is route feasibility evidence, not completed native construction.

All factory connections previously shared `factory:factory_connect:`. A failed
water connection could therefore consume the crude-oil connection's budget.
New identities hash source role, target role, connector kind and fluid; they do
not include ticks or changing material estimates. Unattributed legacy failures
remain a conservative floor for every derived connection identity.

Old campaigns require explicit operator attribution before that floor can be
removed. `connection_failure_attribution` records each legacy count, its complete
allocation to specific identities, and an evidence reference. The old count is
retained, allocated counts must already exist, and their sum must match exactly.
Incomplete or inconsistent attribution fails closed. Automated repair acceptance
cannot introduce or alter attribution, including when an old checkpoint omitted
the field. An operator migration must be separately audited outside an active
repair acceptance, with no ambiguous pending action, using retained attempt and
native evidence; it is not an automatic budget reset.

The separate deployment repository replaces the disabled `/bin/false` repair
command with an account-qualified Codex worker. Native gameplay recovery and
full autonomous repair acceptance still require deployment and live validation.
