"""Chrome discovery, launch, and CDP-port health verification.

See BUILD_SPEC.md §2.1 and §6.1. The single most important rule in this
module: since Chrome 136, ``--remote-debugging-port`` is silently ignored
when Chrome is running on its DEFAULT user-data directory. Chrome starts
normally, shows a window, and never opens the debug port — no error, no
warning. It only works with an explicit, non-default ``--user-data-dir``.
Every function below exists to make that failure loud and diagnosable
instead of a hang. Do not "simplify" this by dropping the health check.
"""
from __future__ import annotations

import asyncio
import os
import platform
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from spark.config import ChromeSettings
from spark.logsetup import get_logger

log = get_logger("browser.launcher")


class ChromeExecutableNotFound(RuntimeError):
    pass


class ChromeDebugPortUnavailable(RuntimeError):
    pass


@dataclass
class LaunchResult:
    host: str
    port: int
    cdp_url: str
    process: subprocess.Popen | None  # None if we attached to one already running
    launched_by_us: bool


def find_chrome_executable(override: str | None = None) -> Path:
    """Locate a Chrome/Chromium executable.

    Priority: an explicit override (Settings) > the ``SPARK_CHROME_EXECUTABLE``
    env var > Windows registry App Paths > standard Windows install
    locations > (non-Windows dev fallback only) a system browser or
    Playwright's own bundled Chromium.
    """
    if override:
        p = Path(override)
        if p.exists():
            return p
        raise ChromeExecutableNotFound(f"Configured Chrome path does not exist: {override}")

    env_override = os.environ.get("SPARK_CHROME_EXECUTABLE")
    if env_override and Path(env_override).exists():
        return Path(env_override)

    if platform.system() == "Windows":
        return _find_chrome_windows()
    return _find_chrome_dev_fallback()


def _find_chrome_windows() -> Path:
    try:
        import winreg  # type: ignore[import-not-found]

        key_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    if value and Path(value).exists():
                        return Path(value)
            except FileNotFoundError:
                continue
    except ImportError:
        pass

    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise ChromeExecutableNotFound(
        "Could not locate chrome.exe. Set chrome.executable_path in Settings, "
        "or the SPARK_CHROME_EXECUTABLE environment variable."
    )


def _find_chrome_dev_fallback() -> Path:
    """Non-Windows only. Spark's shipped target is Windows (BUILD_SPEC §1
    non-goals) — this exists ONLY so the codebase can be developed and
    tested on the Linux/macOS sandbox it was built in. The shipped app must
    never reach this function.
    """
    for name in ("google-chrome-stable", "google-chrome", "chromium-browser", "chromium"):
        found = shutil.which(name)
        if found:
            return Path(found)

    # Ask Playwright's own pinned-revision path first...
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            path = Path(p.chromium.executable_path)
            if path.exists():
                log.warning(
                    "DEV FALLBACK: no system Chrome found; using Playwright's "
                    "bundled Chromium at %s. The shipped Windows app must "
                    "never take this path.",
                    path,
                )
                return path
    except Exception:  # pragma: no cover - best-effort fallback
        pass

    # ...but that path can point at a revision the pip package expects that
    # isn't actually installed (e.g. a sandbox pre-provisioned an older
    # revision than the pinned playwright version wants, with no network
    # access to fetch the new one). Fall back to scanning whatever Chromium
    # build actually exists under PLAYWRIGHT_BROWSERS_PATH.
    browsers_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_root:
        candidates = sorted(Path(browsers_root).glob("chromium-*/chrome-linux*/chrome"))
        if candidates:
            path = candidates[-1]
            log.warning(
                "DEV FALLBACK: using pre-installed Chromium found by scanning "
                "PLAYWRIGHT_BROWSERS_PATH at %s (the pip-pinned revision was "
                "not available). The shipped Windows app must never take "
                "this path.",
                path,
            )
            return path

    raise ChromeExecutableNotFound(
        "Could not locate a Chrome/Chromium executable. Set "
        "chrome.executable_path in Settings, or the SPARK_CHROME_EXECUTABLE "
        "environment variable."
    )


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return False  # something is listening
        except (ConnectionRefusedError, OSError):
            return True


async def _port_serves_chrome_devtools(host: str, port: int) -> bool:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"http://{host}:{port}/json/version", timeout=1.5)
            resp.raise_for_status()
            data = resp.json()
            return "webSocketDebuggerUrl" in data or "Browser" in data
    except Exception:
        return False


async def check_cdp_port(
    host: str, port: int, timeout: float = 20.0, poll_interval: float = 0.25
) -> dict:
    """Poll ``http://host:port/json/version`` until it returns a valid CDP
    payload with a ``webSocketDebuggerUrl``, or raise
    :class:`ChromeDebugPortUnavailable` naming the Chrome-136 cause.

    This is the load-bearing check from BUILD_SPEC §2.1. A bare
    "try to connect once and hope" is exactly the bug this function exists
    to prevent — Chrome can be fully running with a visible window while
    the debug port is never opened.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                resp = await client.get(f"http://{host}:{port}/json/version", timeout=2.0)
                resp.raise_for_status()
                data = resp.json()
                if "webSocketDebuggerUrl" in data:
                    return data
                last_error = RuntimeError(
                    f"/json/version responded without webSocketDebuggerUrl: {data}"
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
            await asyncio.sleep(poll_interval)
    raise ChromeDebugPortUnavailable(
        f"Chrome's DevTools port {port} on {host} never became available within "
        f"{timeout}s. Chrome itself may have started successfully and be showing "
        f"a window right now — this is NOT necessarily a crash. Since Chrome 136, "
        f"--remote-debugging-port is silently ignored when Chrome runs on its "
        f"DEFAULT user-data directory; it only works with an explicit, "
        f"non-default --user-data-dir. If this launch did not pass "
        f"--user-data-dir, that is almost certainly the cause. "
        f"Last error: {last_error}"
    ) from last_error


class ChromeLauncher:
    """Ensures a debuggable Chrome is available, launching one on Spark's
    dedicated automation profile if nothing is already listening.
    """

    def __init__(self, settings: ChromeSettings, extra_args: list[str] | None = None):
        self.settings = settings
        # extra_args exists ONLY for tests/dev sandboxes (e.g. "--no-sandbox"
        # to run Chrome as root in a container, "--headless=new" where there
        # is no display). The shipped app must never pass either: the whole
        # point of Spark is a real, visible browser window the user can sign
        # into and watch.
        self.extra_args = extra_args or []

    async def ensure_running(self) -> LaunchResult:
        host = "127.0.0.1"
        port = self.settings.debug_port

        if await _port_serves_chrome_devtools(host, port):
            log.info("Reusing already-running Chrome DevTools endpoint on port %d", port)
            return LaunchResult(
                host=host,
                port=port,
                cdp_url=f"http://{host}:{port}",
                process=None,
                launched_by_us=False,
            )

        if not _port_is_free(host, port):
            original_port = port
            for candidate in range(port + 1, port + 50):
                if _port_is_free(host, candidate):
                    port = candidate
                    break
            else:
                raise ChromeDebugPortUnavailable(
                    f"Could not find a free port near {original_port}"
                )
            log.warning(
                "Port %d was occupied by something other than Chrome DevTools; "
                "using %d instead",
                original_port,
                port,
            )

        executable = find_chrome_executable(self.settings.executable_path)
        profile_dir = Path(self.settings.profile_dir)
        profile_dir.mkdir(parents=True, exist_ok=True)

        args = [
            str(executable),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",  # NEVER launch without this — see module docstring
            "--no-first-run",
            "--no-default-browser-check",
            *self.extra_args,
        ]
        log.info("Launching Chrome on automation profile: %s", profile_dir)
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        try:
            await check_cdp_port(host, port, timeout=20.0)
        except ChromeDebugPortUnavailable:
            if process.poll() is None:
                process.terminate()
            raise

        return LaunchResult(
            host=host,
            port=port,
            cdp_url=f"http://{host}:{port}",
            process=process,
            launched_by_us=True,
        )

    def shutdown(self, result: LaunchResult) -> None:
        if self.settings.close_on_finish and result.process is not None and result.process.poll() is None:
            log.info("Closing Chrome (chrome.close_on_finish=True)")
            result.process.terminate()
            try:
                result.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                result.process.kill()
        else:
            log.info("Leaving Chrome running (chrome.close_on_finish=%s)", self.settings.close_on_finish)
