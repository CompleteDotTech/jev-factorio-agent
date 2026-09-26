"""CLI: python -m jev_factorio --backend mock --steps 8"""
from __future__ import annotations

import argparse
import os
from contextlib import ExitStack
from pathlib import Path

from dotenv import load_dotenv

from .backends.mock import MockBackend
from .loop import AgentLoop
from .research_log import ResearchLog, RunConfiguration, validate_output_paths


def make_backend(name: str, resume: bool = False, adopt_session: bool = False):
    if name == "mock":
        return MockBackend()
    if name == "play_api":
        from .backends.play_api import PlayApiBackend
        return PlayApiBackend(factorio_user_dir=os.environ.get(
            "FACTORIO_USER_DIR", "~/.factorio"))
    if name == "fle":
        from .backends.fle import FleBackend
        b = FleBackend()
        b.start(resume=resume, adopt_session=adopt_session)
        return b
    raise SystemExit(f"unknown backend: {name}")


def cli() -> None:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    p = argparse.ArgumentParser(prog="jev-factorio")
    p.add_argument("--backend", default=os.environ.get("JEV_BACKEND", "mock"))
    limits = p.add_mutually_exclusive_group()
    limits.add_argument("--steps", type=int)
    limits.add_argument("--duration-hours", type=float)
    p.add_argument("--resume", action="store_true",
                   help="Resume an existing live FLE session without resetting its world")
    p.add_argument("--tick-seconds", type=float,
                   default=float(os.environ.get("JEV_TICK_SECONDS", "0")))  # 0 in mock
    p.add_argument("--confidence-floor", type=float,
                   default=float(os.environ.get("JEV_CONFIDENCE_FLOOR", "0.45")))
    p.add_argument("--log-file", default=os.environ.get("JEV_LOG_FILE"))
    p.add_argument("--run-dir", default=os.environ.get("JEV_RUN_DIR"),
                   help="Create a new, exclusive research evidence directory (never append/resume)")
    p.add_argument("--dashboard-events", default=os.environ.get("JEV_DASHBOARD_EVENTS"),
                   help="Optional best-effort live dashboard JSONL; hierarchical controller only")
    p.add_argument("--controller", choices=("flat", "hierarchical"), default="flat")
    p.add_argument("--factory-scheduling", choices=("serial", "ready-work"), default="serial",
                   help="Opt-in bounded production choices; does not enable concurrent mutations or belts")
    p.add_argument("--campaign-diagnostics", action="store_true",
                   help="30-minute progress, host pressure, eligibility and blocked-investment evidence")
    p.add_argument("--profile-observations", action="store_true",
                   help="Content-free observation RPC timing; hierarchical FLE only")
    p.add_argument("--consolidated-observations", action="store_true",
                   help="DEV PILOT: batch native discovery with identity-checked cache; implies profiling")
    p.add_argument("--lead-time-supply", action="store_true",
                   help="DEV PILOT: bounded current-science replenishment reserves; requires ready-work")
    p.add_argument("--coverage-margin-lookahead", action="store_true",
                   help="DEV PILOT: measured coverage permits only already-produced future science collection")
    p.add_argument("--furnace-output-buffers", action="store_true",
                   help="Opt-in paid burner-inserter output buffers; requires ready-work FLE")
    p.add_argument("--furnace-input-belts", action="store_true",
                   help="Opt-in owned drill/belt input routes; requires furnace output buffers")
    p.add_argument("--ore-side-successors", action="store_true",
                   help="Opt-in additive ore-side producers; requires background work and input belts")
    p.add_argument("--mining-outposts", action="store_true",
                   help="Opt-in paid mining outposts for existing manual cells; requires furnace input belts")
    p.add_argument("--background-work", action="store_true",
                   help="Opt-in receipt-tracked crafting and research prefetch; requires ready-work FLE")
    p.add_argument("--target", choices=("bootstrap_mining", "iron_smelting", "steam_power",
                                       "automation_science", "rocket_launch"), default="rocket_launch")
    p.add_argument("--policy", choices=("jev", "deterministic", "hybrid"), default="jev")
    p.add_argument("--mock-model", action="store_true", help="Explicit offline model (mock backend only)")
    p.add_argument("--model", help="Provider-specific model ID; pin it for reproducible evaluation")
    p.add_argument("--checkpoint", help="Session-bound controller checkpoint, not a game save")
    p.add_argument("--resume-controller", action="store_true")
    p.add_argument("--adopt-session", action="store_true",
                   help="Explicitly identify an older live FLE session without resetting it")
    args = p.parse_args()
    if args.consolidated_observations:
        args.profile_observations = True
    if args.profile_observations or args.lead_time_supply or args.coverage_margin_lookahead:
        args.campaign_diagnostics = True
    if args.campaign_diagnostics and args.controller != "hierarchical":
        p.error("--campaign-diagnostics requires --controller hierarchical")
    if args.profile_observations and args.backend != "fle":
        p.error("Observation profiling requires --backend fle")
    if args.lead_time_supply and args.factory_scheduling != "ready-work":
        p.error("--lead-time-supply requires --factory-scheduling ready-work")
    if args.coverage_margin_lookahead and not args.lead_time_supply:
        p.error("--coverage-margin-lookahead requires --lead-time-supply")
    if args.duration_hours is not None and (
        not 0 < args.duration_hours < float("inf")
    ):
        p.error("--duration-hours must be finite and positive")
    if args.tick_seconds < 0 or not args.tick_seconds < float("inf"):
        p.error("--tick-seconds must be finite and nonnegative")
    if args.resume and args.backend != "fle":
        p.error("--resume requires --backend fle")
    if args.adopt_session and (
        args.controller != "hierarchical" or args.backend != "fle"
        or not args.resume or args.resume_controller
    ):
        p.error("--adopt-session requires hierarchical FLE --resume and a new checkpoint")
    if args.steps is not None and args.steps < 0:
        p.error("--steps must be nonnegative")
    if not 0 <= args.confidence_floor <= 1:
        p.error("--confidence-floor must be finite and in [0, 1]")
    if args.backend not in {"mock", "play_api", "fle"}:
        p.error(f"unknown backend: {args.backend}")
    if args.dashboard_events and args.controller != "hierarchical":
        p.error("--dashboard-events requires --controller hierarchical")
    if args.factory_scheduling != "serial" and args.controller != "hierarchical":
        p.error("--factory-scheduling requires --controller hierarchical")
    if args.background_work and (
        args.controller != "hierarchical" or args.factory_scheduling != "ready-work"
        or args.backend != "fle" or args.target == "bootstrap_mining"
    ):
        p.error("--background-work requires hierarchical FLE ready-work and a native production target")
    if args.ore_side_successors and (not args.furnace_input_belts or not args.background_work
                                     or args.mining_outposts or args.target != 'rocket_launch'
                                     or not args.resume or not args.resume_controller):
        p.error('--ore-side-successors requires background-work input belts, rocket goal, existing resumed campaign, and no mining outposts')
    if args.mining_outposts and (not args.furnace_input_belts or args.target != 'rocket_launch'):
        p.error('--mining-outposts requires --furnace-input-belts and --target rocket_launch')
    if args.furnace_input_belts and not args.furnace_output_buffers:
        p.error("--furnace-input-belts requires --furnace-output-buffers")
    if args.furnace_output_buffers and (
        args.controller != "hierarchical" or args.factory_scheduling != "ready-work"
        or args.backend != "fle" or args.target == "bootstrap_mining"
    ):
        p.error("--furnace-output-buffers requires hierarchical FLE ready-work and a native production target")
    options = dict(confidence_floor=args.confidence_floor,
                   tick_seconds=args.tick_seconds, log_file=args.log_file)
    if args.controller == "flat":
        if args.mock_model or args.checkpoint or args.resume_controller or args.model or args.policy != "jev":
            p.error("Campaign options require --controller hierarchical")
    else:
        from .controller import HierarchicalLoop
        from .jev_client import MockJevClient, make_client

        if args.backend not in {"mock", "fle"}:
            p.error("Hierarchical control currently supports mock and FLE backends")
        if args.mock_model and (args.backend != "mock" or args.policy == "deterministic"):
            p.error("--mock-model requires --backend mock and a model-based policy")
        if args.backend != "mock" and (not args.checkpoint or args.tick_seconds <= 0):
            p.error("Live hierarchical control requires --checkpoint and a positive --tick-seconds")
        if args.checkpoint and Path(args.checkpoint).exists() and not args.resume_controller:
            p.error("Checkpoint exists; explicitly resume or use a new path")
        if args.resume_controller:
            if not args.checkpoint or not Path(args.checkpoint).is_file():
                p.error("--resume-controller requires an existing --checkpoint")
            if args.backend == "fle" and not args.resume:
                p.error("Resuming live controller memory requires --resume to preserve the world")
        if args.ore_side_successors:
            try:
                import json
                from .background import BackgroundWorkLoop
                from .buffer_controller import buffered_loop_type
                from .input_controller import input_loop_type
                from .successor_controller import successor_loop_type
                path = Path(args.checkpoint)
                identity = json.loads(path.read_text(encoding='utf-8'))
                kind = successor_loop_type(input_loop_type(buffered_loop_type(BackgroundWorkLoop)))
                kind.memory_type.load(path, identity.get('session_id'), args.target)
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                p.error('Successor checkpoint preflight failed; backend not started')
        # Resolve credentials before starting a backend that initializes a world.
        try:
            client = (None if args.policy == "deterministic" else
                      MockJevClient() if args.mock_model else
                      make_client(allow_mock=False, model=args.model))
        except ValueError as error:
            p.error(str(error))

    if args.dashboard_events:
        dashboard_path = Path(args.dashboard_events)
        if dashboard_path.is_symlink():
            p.error("Dashboard output must not be a symlink")
        for other in (args.checkpoint, args.log_file):
            if other and (
                str(dashboard_path.resolve()).casefold() == str(Path(other).resolve()).casefold()
                or (dashboard_path.exists() and Path(other).exists() and dashboard_path.samefile(other))
            ):
                p.error("Dashboard output must be separate from logs and checkpoints")
    run_dir = None
    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
        try:
            validate_output_paths(run_dir, args.log_file, args.checkpoint, args.dashboard_events)
        except ValueError as error:
            p.error(str(error))
        configuration = RunConfiguration(
            backend=args.backend, controller=args.controller, policy=args.policy,
            target=args.target if args.controller == "hierarchical" else None,
            requested_model=args.model,
            steps=(args.steps if args.steps is not None else 8) if args.duration_hours is None else None,
            duration_seconds=args.duration_hours * 3600 if args.duration_hours is not None else None,
            tick_seconds=args.tick_seconds, confidence_floor=args.confidence_floor,
            resume=args.resume, resume_controller=args.resume_controller,
            adopt_session=args.adopt_session, mock_model=args.mock_model,
            legacy_log_enabled=bool(args.log_file), checkpoint_enabled=bool(args.checkpoint),
            factory_scheduling=args.factory_scheduling, background_work=args.background_work,
            furnace_output_buffers=args.furnace_output_buffers,
            furnace_input_belts=args.furnace_input_belts, mining_outposts=args.mining_outposts,
            ore_side_successors=args.ore_side_successors,
            campaign_diagnostics=args.campaign_diagnostics,
            profile_observations=args.profile_observations,
            consolidated_observations=args.consolidated_observations,
            lead_time_supply=args.lead_time_supply,
            coverage_margin_lookahead=args.coverage_margin_lookahead,
        )
    with ExitStack() as cleanup:
        research = None
        if run_dir is not None:
            try:
                research = cleanup.enter_context(ResearchLog(run_dir, configuration))
            except (OSError, ValueError) as error:
                p.error(f"Cannot initialize research evidence ({type(error).__name__}); backend not started")
        writer = None
        if args.dashboard_events:
            from .dashboard import EventWriter
            try:
                writer = cleanup.enter_context(EventWriter(
                    args.dashboard_events, forbidden=(args.checkpoint, args.log_file)))
            except (OSError, ValueError) as error:
                p.error(str(error))
        if args.controller == "flat":
            options["research_log"] = research
            loop = AgentLoop(make_backend(args.backend, resume=args.resume), **options)
        else:
            options["research_log"] = research
            loop_type = HierarchicalLoop
            if args.background_work:
                from .background import BackgroundWorkLoop

                loop_type = BackgroundWorkLoop
            if args.furnace_output_buffers:
                from .buffer_controller import buffered_loop_type

                loop_type = buffered_loop_type(loop_type)
            if args.furnace_input_belts:
                from .input_controller import input_loop_type

                loop_type = input_loop_type(loop_type)
            if args.ore_side_successors:
                from .successor_controller import successor_loop_type
                loop_type = successor_loop_type(loop_type)
            if args.mining_outposts:
                from .outpost_controller import outpost_loop_type
                loop_type = outpost_loop_type(loop_type)
            if args.campaign_diagnostics:
                from .campaign_controller import campaign_loop_type
                loop_type = campaign_loop_type(loop_type)
                options.update(lead_time_supply=args.lead_time_supply,
                               coverage_margin_lookahead=args.coverage_margin_lookahead)
            if args.backend == "fle":
                from .operational_safety import storage_ready
                output_roots = [Path(args.checkpoint).parent]
                if args.log_file:
                    output_roots.append(Path(args.log_file).parent)
                if run_dir:
                    output_roots.append(run_dir)
                if not storage_ready(output_roots):
                    p.error("Storage reserve unavailable; live backend was not attached")
            backend = make_backend(args.backend, resume=args.resume, adopt_session=args.adopt_session)
            if args.backend == "fle" and args.profile_observations:
                backend.profile_observations = True
                backend.consolidated_observations = args.consolidated_observations
            loop = loop_type(backend, jev=client,
                                    target=args.target, policy=args.policy, checkpoint=args.checkpoint,
                                    resume_controller=args.resume_controller,
                                    factory_scheduling=args.factory_scheduling, **options)
        if writer is not None:
            from .dashboard import attach
            attach(loop, writer)
        if research is not None:
            memory = getattr(loop, "memory", None)
            research.emit("controller_initialized", {
                "requested_model": getattr(getattr(loop, "jev", None), "model", None),
                "model_is_mock": bool(getattr(getattr(loop, "jev", None), "is_mock", False)),
            }, session_id=getattr(memory, "session_id", None))
        try:
            if args.duration_hours is not None:
                loop.run(steps=None, duration_seconds=args.duration_hours * 3600)
            else:
                loop.run(steps=args.steps if args.steps is not None else 8)
        except BaseException as error:
            from .recovery_policy import record_exit
            record_exit(loop, error)
            raise
        if research is not None:
            research.emit("controller_stopped", {
                "terminal": bool(getattr(loop, "terminal", False)),
                "controller_status": getattr(getattr(loop, "memory", None), "status", None),
            })


if __name__ == "__main__":
    cli()
