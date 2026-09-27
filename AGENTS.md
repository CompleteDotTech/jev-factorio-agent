# Repository Guidelines

## Recover stopped runs and keep the live overlay truthful

When the user asks whether an expected ongoing agent or campaign is running,
verify live state. If it unexpectedly stopped, stalled, or became blocked, treat
the question as authorization to diagnose, repair, and safely resume that run.
Do not stop at a status report or ask whether routine recovery should proceed.
Verify fresh useful progress after recovery.

Update the live overlay during recovery and verify it after resumption. In OBS
Studio Mode, selecting a scene changes Preview; verify the actual Program output
before claiming the broadcast changed. Preserve streaming and audio settings.

Coordinate with the existing supervisor and service owner. Preserve original
identity/cutoff, pending actions, receipts, ownership, failure history, and
unrelated work. Never launch a duplicate controller, blindly retry an ambiguous
mutation, or bypass reconciliation, durability, review, or deployment gates.
An intentional user stop, completion, or expired authorized window does not
authorize a restart or extension. Finish independent authorized work and report
the precise blocker if required evidence, access, or authority is unavailable.

Never run `wsl --shutdown`, `wsl --terminate`, or `wsl -t` on the shared machine.
Diagnose the affected service without restarting shared hosts or disrupting
other sessions. See [the recovery runbook](docs/STATUS_RECOVERY.md).

## Project Structure & Module Organization

This Python 3.10+ project uses a `src` layout. Application code lives in
`src/jev_factorio/`: `state.py` defines snapshots, `questions.py` filters actions
and builds typed questions, `jev_client.py` provides model clients, and `loop.py`
coordinates decisions. CLI entry points are in `main.py` and `__main__.py`.
`backends/` contains the backend protocol, working mock, and unfinished
`play_api` and `fle` integrations. Tests live in `tests/`; architecture and
implementation plans live in `docs/`. There is no dedicated game-asset directory.

## Build, Test, and Development Commands

- `python -m venv .venv && source .venv/bin/activate`: create and activate a local environment.
- `python -m pip install -e .`: install the package and `jev-factorio` CLI for development.
- `python -m pip install pytest`: install the test runner, which is not declared in `pyproject.toml`.
- `PYTHONPATH=src python -m pytest tests/`: run the test suite.
- `PYTHONPATH=src python -m jev_factorio --backend mock --steps 8 --tick-seconds 0`: run eight simulated decisions. Unset `TYPESAFE_API_KEY` for an offline run.

Packaging uses setuptools via `pyproject.toml`; no custom build script is present.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, `snake_case` functions and
modules, and `PascalCase` classes. Use type annotations for public interfaces and
dataclasses for structured state. Keep model-facing snapshots compact.
Filter impossible actions before presenting choices to Jev; deterministic code
owns game rules and actuation. Implement backend changes through `observe()` and
`act()`. No formatter or linter is configured.

## Testing Guidelines

Use pytest-discovered `tests/test_*.py` files and `test_*` functions. Existing
tests cover candidate filtering, fallback behavior, and a mock loop step.
Add focused tests for changed behavior, using `MockBackend` and `MockJevClient`
to avoid network calls and game dependencies. No coverage threshold is configured.
Report real-game validation separately from mock results.

## Commit & Pull Request Guidelines

The short Git history uses descriptive subjects such as
`Initial skeleton: Jev-driven System One Factorio agent (mock-verified)`;
no formal commit convention is established. Use concise, action-oriented subjects.
In PRs, describe the behavior change, link relevant issues, list validation
commands and results, and identify incomplete backend work.

## Configuration & Secrets

Use `.env.example` as a reference. The CLI loads `.env` from the current working
directory without overriding already-exported environment variables. Never commit
API credentials; `.env` is gitignored and holds real keys. Update the README or
architecture documentation when changing configuration or backend contracts.
