# Ready science and optional coal-kit ranking

When an unstarted opt-in coal-kit acquisition competes with an executable
ready pack required by current research, the coal controller defers the kit
from that decision's executable frontier. It records
`selection_deferred_reason: ready_required_science` in diagnostics. This
applies to deterministic and model selection: a model cannot repeatedly pick
an optional kit while a ready required pack is available. A started paid kit
keeps its durable continuation. The kit becomes eligible again when ready
science is no longer executable. Urgent boiler or required-producer maintenance
retains its existing candidate path.

The coal controller records the exact kit offer on the current in-memory
snapshot after compiling it. Ranking accepts only that unchanged one-step
offer at the same observation tick. A copied label on another plan, edited
cost evidence, or an old observation cannot grant priority. Dispatch still
checks the durable funding, receipt, ownership, and native preconditions.

`tests/test_coal_ready_science_ranking.py` exercises the ordinary composed
controller against a synthetic API-shaped backend. It proves two consecutive
ready-science pickups under deterministic and kit-seeking model policies,
checks kit reentry and paid continuation, and rejects stale or edited ranking
evidence. It does not prove installed Factorio timing,
science consumption, coal-network profitability, or campaign recovery.
