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


async def _run_task_async(task_path: Path) -> int:
    from spark.browser.launcher import ChromeLauncher
    from spark.browser.session import BrowserSession
    from spark.orchestrator import Orchestrator, RunOutcome
    from spark.reasoning.provider import build_provider
    from spark.runlog.recorder import RunRecorder, purge_old_runs
    from spark.scripts.model import load_task

    task = load_task(task_path)
    settings = load_settings()

    launcher = ChromeLauncher(settings.chrome)
    launch_result = await launcher.ensure_running()
    session = await BrowserSession.attach(launch_result.cdp_url, prefer_url_substring=task.start_url)
    try:
        provider = build_provider(settings.active_provider, settings.provider_settings())
        recorder = RunRecorder(task=task, settings=settings)
        orchestrator = Orchestrator(task=task, settings=settings, provider=provider, session=session, recorder=recorder)
        result = await orchestrator.run()
        log.info("Run finished: %s — %s (artefacts: %s)", result.outcome.value, result.message, recorder.run_dir)
        purge_old_runs(settings.retention)
        return 0 if result.outcome == RunOutcome.SUCCESS else 1
    finally:
        await session.close()
        launcher.shutdown(launch_result)


def _cmd_run(args: argparse.Namespace) -> int:
    return asyncio.run(_run_task_async(Path(args.task)))


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
