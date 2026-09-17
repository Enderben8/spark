"""CLI entry point: ``python -m spark`` or the installed ``spark`` script.

Two ways in:

* ``spark`` with no args launches the GUI (PySide6). This is the primary
  path for the shipped Windows app.
* ``spark run <task.yaml>`` runs a task headlessly from the terminal — no
  GUI required. Useful for development, CI, and anyone who prefers a
  terminal.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from spark import __version__
from spark.config import SETTINGS_PATH, ensure_data_dirs, load_settings
from spark.logsetup import configure_logging, get_logger

log = get_logger("main")


def _cmd_run(args: argparse.Namespace) -> int:
    from spark.orchestrator import Orchestrator, RunOutcome
    from spark.scripts.model import load_task

    task = load_task(Path(args.task))
    settings = load_settings()
    orchestrator = Orchestrator(task=task, settings=settings)
    result = asyncio.run(orchestrator.run())
    log.info("Run finished: %s", result.outcome)
    return 0 if result.outcome == RunOutcome.SUCCESS else 1


def _cmd_gui(_args: argparse.Namespace) -> int:
    from spark.gui.main_window import run_gui

    return run_gui()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spark")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run a task headlessly, no GUI")
    p_run.add_argument("task", help="Path to a task YAML file")
    p_run.set_defaults(func=_cmd_run)

    p_gui = sub.add_parser("gui", help="Launch the desktop GUI (also the default)")
    p_gui.set_defaults(func=_cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    ensure_data_dirs()
    settings = load_settings()
    configure_logging(settings.log_level)
    log.info("Spark %s — settings: %s", __version__, SETTINGS_PATH)

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        return _cmd_gui(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
