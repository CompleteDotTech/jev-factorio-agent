# jev-factorio

A Jev-powered Factorio agent. Jev (TypeSafe AI's System One model) makes the
fast macro decisions - goal, next action, stuck detection - as typed
Choice/Score/Noul questions; deterministic code owns game rules, option
filtering, and actuation. To our knowledge this is the first Jev-driven
game agent.

Docs: [ARCHITECTURE](docs/ARCHITECTURE.md) | [BUILD PLAN](docs/BUILD_PLAN.md)

For bounded native runs with error-triggered Codex repair and guarded relaunch,
see [Autonomous campaign supervision](docs/AUTONOMOUS_SUPERVISION.md).

## Quick start (offline, no key, no game)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
PYTHONPATH=src python -m jev_factorio --backend mock --steps 8 --tick-seconds 0
```

With a real key (`TYPESAFE_API_KEY` in the environment) the same loop calls
`jev-latest` at `https://api.typesafe.ai/v1/systemone`.

## Local configuration

The CLI loads `.env` from the current working directory before reading settings.
Run from the repository root and set `TYPESAFE_API_KEY` in that file.
Exported environment variables take precedence. `.env` is ignored by Git;
keep its permissions restricted (`chmod 600 .env`) and never commit credentials.
Re-run `pip install -e .` after updating to install the dotenv dependency.

`--backend mock` simulates the game but still uses a configured API key.
For a fully offline run, override all provider credentials:

```bash
TYPESAFE_API_KEY= CLOUDFLARE_API_TOKEN= PYTHONPATH=src python -m jev_factorio --backend mock --steps 8 --tick-seconds 0
```

## Research logging (opt-in)

`--run-dir` (or `JEV_RUN_DIR`) creates a new exclusive evidence directory with a
secret-safe manifest, fsynced SHA-256-chained lifecycle events, and a final seal.
It never reuses an existing directory. The original `--log-file` remains
independently optional and retains its existing decision JSONL format.

```bash
python -m jev_factorio --controller hierarchical --backend mock --mock-model \
  --target bootstrap_mining --steps 40 --tick-seconds 0 \
  --run-dir runs/research-001 --log-file runs/research-001/decisions.jsonl
python -m jev_factorio.research_log runs/research-001
```

Controller call boundaries additionally record causal observations, decisions,
actions, verification, and attempt joins; see [causal logging](docs/CAUSAL_LOGGING.md).
This evidence is not a complete native action
trace. A valid seal proves internal consistency, not successful gameplay or
independent authenticity. See [Research logging](docs/RESEARCH_LOGGING.md) for
schemas, verification, incomplete runs, redaction and durability limits. Use a
new directory for each invocation; supervisor directory rotation is not yet
implemented, so do not export a fixed `JEV_RUN_DIR` to a supervised campaign.

## Layout

- `src/jev_factorio/state.py` - GameSnapshot + compact Jev-facing state
- `src/jev_factorio/questions.py` - typed question builders + candidate-action filter
- `src/jev_factorio/jev_client.py` - SDK/HTTP client + offline MockJevClient
- `src/jev_factorio/loop.py` - observe -> ask -> gate on confidence -> act
- `src/jev_factorio/backends/` - mock, dedicated-world FLE adapter, play_api (skeleton)

## Live Factorio

Install the optional adapter with `pip install -e '.[fle]'`. Configure the
`FACTORIO_RCON_*` variables in `.env` and keep the RCON connection private.
Only use a dedicated, disposable agent world: starting the adapter resets
characters, inventory, and factory entities. It refuses to start unless that
world has been explicitly marked through RCON:

```lua
/sc storage.jev_factorio_session = true
```

Save the marked world before starting the adapter, and configure the server to
load that save on restart. Never mark a personal gameplay world.

```bash
.venv/bin/python -m jev_factorio --backend fle --steps 12 \
  --tick-seconds 2 --log-file runs/native.jsonl
```

This bootstrap gathers coal, places an iron drill and output chest, and fuels
production. Player actions use native walking and mining states at 1× speed.
Buildings consume existing cursor items through ordinary build checks; transfers
require native interaction reach. FLE's teleporting movement, scripted harvesting,
and connection-building shortcuts are not used.
Decision logs distinguish Jev choices from confidence-gated scripted fallbacks.
FLE's executable Lua state stays in a session-only table, not Factorio's saved
`storage`, so saving does not try to serialize functions. Saved factories persist,
but the agent session does not resume across server reloads; starting another run
resets the dedicated world again. Keep the viewer connected before initializing
the agent; reconnecting viewers during a session is not yet validated.

Native actions select player 1 by default. An integration adopting an existing
connected character can set `jev_fle_runtime.jev_player_index` to a positive
integer before loading the native action modules. The logical FLE agent slot
`jev_fle_runtime.agent_characters[1]` must still identify that character. This is
an internal runtime setting, not a CLI option. The first module load locks the
selection for that runtime; changing it or selecting a different player on
reattachment fails closed. Cleanup stops the originally selected character, and
the adapter does not fall back to another connected player.

To continue a live agent session for 12 hours instead of a fixed number of steps:

```bash
.venv/bin/python -u -m jev_factorio --backend fle --resume \
  --duration-hours 12 --tick-seconds 2 --log-file runs/12-hour.jsonl
```

`--resume` refuses to reset if the live session is missing. Duration mode stops
starting decisions at its monotonic deadline; an in-flight decision may finish
afterward. It makes ongoing API calls and remains limited to the bootstrap
actions above, not full-game progression.

## Hierarchical controller (opt-in)

The [hierarchical controller guide](docs/HIERARCHICAL_CONTROLLER.md) describes
persistent goals, batched JEV candidate evaluation, verified skill plans,
checkpoint recovery, and deterministic comparison runs.

The [performance investigation prompt](docs/PERFORMANCE_INVESTIGATION_PROMPT.md)
defines a persistent, evidence-backed optimization study with an explicit PR approval gate.

```bash
python -m jev_factorio --controller hierarchical --backend mock --mock-model \
  --target bootstrap_mining --steps 40 --tick-seconds 0 \
  --checkpoint runs/bootstrap-state.json --log-file runs/bootstrap.jsonl
```

The FLE hierarchical controller now compiles native production and research
plans for `iron_smelting`, `steam_power`, `automation_science`, and `rocket_launch`.
It reads recipes, technology costs, and machine capabilities from the running
base game rather than assuming fixed research costs. The existing flat controller
stays the default and remains bootstrap-only.

```bash
python -m jev_factorio --controller hierarchical --backend fle --resume \
  --target rocket_launch --policy hybrid --duration-hours 12 \
  --checkpoint runs/campaign-state.json --log-file runs/campaign.jsonl
```

`hybrid` records JEV abstentions and uses a deterministic compiled plan when
needed; `jev` remains strict. The agent carries solids between machines and builds
physical fluid and electricity connections. Native crafting, inventories,
research, and the force rocket-launch counter verify progress. Movement and
resource gathering still use FLE acceleration, not keyboard/mouse gameplay.

**Experimental: no complete native rocket-launch playthrough is verified.**
Native hand-crafting requires a connected viewer controlling the agent character.
Live-session resume does not support server reloads or viewer reconnection;
do not reset the world or discard a pending checkpoint to work around a failure.
The implementation is restricted to the exported Factorio 2.0 base-game catalog;
unsupported mods, recipes, or bounded exploration failures stop explicitly.

## Author

Built by **Timothy Wayne Gregg** (CompleteTech LLC, Cincinnati, OH).

- GitHub: <https://github.com/CompleteDotTech>
- Website: <https://www.complete.tech>
- Email: timothy.gregg@complete.tech

## License

MIT - see [LICENSE](LICENSE). Copyright 2026 Timothy Wayne Gregg (CompleteTech LLC).

This project derives from `Adibrill1/jev-factorio`, which publishes no license
file. The MIT grant here covers CompleteTech's own additions and modifications;
confirm the upstream terms with that project's author before reusing the
inherited skeleton elsewhere.

### Mining automation for existing manual cells

The opt-in `--mining-outposts` extension adds paid iron/copper drill-to-chest
outposts when an established furnace lacks a supported direct input route.
Existing furnaces and receipts remain unchanged; ore is collected only after
native flow evidence and then hauled in batches. See
[Mining outposts](docs/MINING_OUTPOSTS.md) for required controller flags,
idle-boundary checkpoint enablement, supervisor handoff limits, and native
benchmark requirements. This is not full belt transport or a live deployment.

## Planning efficiency and staged autonomy

The [bounded planning and autonomy plan](docs/PLANNING_AUTONOMY.md) documents
snapshot-local planning improvements, safe additive expansion requirements, and
development VM acceptance gates. The [offline evidence audit](docs/EVIDENCE_AUDIT.md)
verifies capture hashes and reports measurement coverage without contacting a
backend or treating missing gameplay metrics as zero. Source integration is not
production cutover or proof of native throughput improvement.
