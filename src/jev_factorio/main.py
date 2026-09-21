"""CLI: python -m jev_factorio --backend mock --steps 8"""
from __future__ import annotations

import argparse
import os
from contextlib import ExitStack
from pathlib import Path

from dotenv import load_dotenv

from .backends.mock import MockBackend
from .loop import AgentLoop


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
    p.add_argument("--dashboard-events", default=os.environ.get("JEV_DASHBOARD_EVENTS"),
                   help="Optional best-effort live dashboard JSONL; hierarchical controller only")
    p.add_argument("--controller", choices=("flat", "hierarchical"), default="flat")
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
    if args.dashboard_events and args.controller != "hierarchical":
        p.error("--dashboard-events requires --controller hierarchical")
    with ExitStack() as cleanup:
        writer = None
        if args.dashboard_events:
            from .dashboard import EventWriter
            try:
                writer = cleanup.enter_context(EventWriter(
                    args.dashboard_events, forbidden=(args.checkpoint, args.log_file)))
            except (OSError, ValueError) as error:
                p.error(str(error))
        options = dict(confidence_floor=args.confidence_floor,
                       tick_seconds=args.tick_seconds, log_file=args.log_file)
        if args.controller == "flat":
            if args.mock_model or args.checkpoint or args.resume_controller or args.model or args.policy != "jev":
                p.error("Campaign options require --controller hierarchical")
            loop = AgentLoop(make_backend(args.backend, resume=args.resume), **options)
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
            # Resolve credentials before starting a backend that initializes a world.
            try:
                client = (None if args.policy == "deterministic" else
                          MockJevClient() if args.mock_model else
                          make_client(allow_mock=False, model=args.model))
            except ValueError as error:
                p.error(str(error))
            loop = HierarchicalLoop(make_backend(args.backend, resume=args.resume,
                                                 adopt_session=args.adopt_session), jev=client,
                                    target=args.target, policy=args.policy, checkpoint=args.checkpoint,
                                    resume_controller=args.resume_controller, **options)
        if writer is not None:
            from .dashboard import attach
            attach(loop, writer)
        if args.duration_hours is not None:
            loop.run(steps=None, duration_seconds=args.duration_hours * 3600)
        else:
            loop.run(steps=args.steps if args.steps is not None else 8)


if __name__ == "__main__":
    cli()
