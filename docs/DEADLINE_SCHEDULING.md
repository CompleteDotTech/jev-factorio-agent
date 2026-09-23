# Deadline-aware research work

Ready-work scheduling uses a bounded estimate of replenishment lead time rather
than waiting exclusively for the lab's pack count to fall below five. The supply
quantities remain capped at twenty per request and by remaining research.
Catalog recipe energy and machine speed contribute to an explicitly conservative
serial estimate. Travel, gathering, service time, and safety margin are declared
policy heuristics in `planning/scheduling.py`, not measured engine constants.
Unknown locations or unsupported production produce an unknown lead estimate,
not a zero-time forecast. The existing low-stock trigger remains available.

Each pack row records available and remaining stock, estimated coverage and lead
time, refill deadline, whether refill is due, and the estimate basis. The active
planner uses these rows in both ordinary foreground scheduling and background
independent work. If all remaining current research packs are already in the lab,
one next research batch may be prepared through a bounded dependency preview.
The preview does not cancel current research or assume a future recipe unlock.
Current lab commitments are not spent again on the next technology.

Small research-progress waits are coalesced into larger native progress watches,
with a bounded maintenance heartbeat and refill deadline. Plan materials retain
the schedule reason and estimated next check tick for audit. Acknowledged passive
research waits may back off observation cadence, up to fifteen seconds, inside
the original campaign wall deadline. Cadence is chosen after each step. Prepared,
ambiguous, mutating, uncertain, or unannotated waits retain the original cadence;
zero-delay offline tests remain zero-delay. Existing wait-yield mechanisms still
allow new executable work to preempt a passive wait.

A forecast is not a receipt. Elapsed time never verifies progress: the native
research predicate, background craft receipt, original action timeout, job output
locks, and no-replay barriers remain authoritative. Serial scheduling is unchanged.
The single actor is not dispatched concurrently. Cadence reduction is an overhead
change; starvation reduction and useful overlap must be measured independently.

`tests/test_deadline_scheduling.py` covers early refill, sufficient/unknown supply,
next-batch preparation, dependency cycles, locked recipes, foreground scheduling,
coalesced native verification, and cadence exclusions for ambiguous actions. The
unit matrix now also tests CPython 3.13 and retains its already-declared Lua test
wheel with a checksum so the same Lua fixtures can be reproduced offline. This
adds test coverage, not a runtime dependency or live-game validation claim.

## Productive preparation and input coverage

Ready-work also separates immediate raw requirements from optional stockpiles,
services relevant producers before their inputs run out, and prepares a bounded
extra current-research batch while the lab is supplied. See
[Stage 1 productive scheduling](PRODUCTIVE_SCHEDULING.md) for bounds, forecast
accounting, wait-yield behavior, and native validation requirements. These changes
do not alter the deadline, cadence, receipt, or pending-action contracts above.
