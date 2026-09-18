"""M2 acceptance test (BUILD_SPEC.md milestone table): from a cold start,
Spark launches Chrome on the automation profile, verifies the port, attaches,
and reports the page title. Killing the port produces the named error, not a
hang.

Runs against whatever Chrome/Chromium find_chrome_executable() locates — on
this dev sandbox that is Playwright's own bundled Chromium (see the
DEV FALLBACK warning in browser/launcher.py); the shipped Windows app must
locate a real system Chrome instead. The launch mechanics under test
(--remote-debugging-port + --user-data-dir, and the /json/version health
check) are identical either way.
"""
from __future__ import annotations

import socket
import time

import pytest

from spark.browser.launcher import (
    ChromeDebugPortUnavailable,
    ChromeLauncher,
    check_cdp_port,
)
from spark.browser.session import BrowserSession
from spark.config import ChromeSettings

# This dev/CI sandbox runs Chrome as root with no display server, neither of
# which is true of the shipped Windows product (a real user, a real visible
# window). These flags exist ONLY to make Chrome launchable in that sandbox —
# see the extra_args docstring in ChromeLauncher.__init__.
_SANDBOX_ONLY_ARGS = ["--no-sandbox", "--headless=new"]


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_launch_attach_and_read_title(tmp_path):
    settings = ChromeSettings(
        debug_port=_free_tcp_port(),
        profile_dir=str(tmp_path / "chrome-profile"),
    )
    launcher = ChromeLauncher(settings, extra_args=_SANDBOX_ONLY_ARGS)
    result = await launcher.ensure_running()
    try:
        assert result.launched_by_us is True
        assert result.process is not None and result.process.poll() is None

        session = await BrowserSession.attach(result.cdp_url)
        try:
            await session.goto("data:text/html,<title>Spark Test Page</title><h1>hi</h1>")
            await session.wait_for_settle(max_wait_s=3.0)
            title = await session.evaluate("() => document.title")
            assert title == "Spark Test Page"
        finally:
            await session.close()
    finally:
        launcher.shutdown(result)
        if result.process is not None:
            result.process.terminate()
            try:
                result.process.wait(timeout=5)
            except Exception:
                result.process.kill()


@pytest.mark.asyncio
async def test_health_check_fails_loudly_when_nothing_is_listening():
    dead_port = _free_tcp_port()  # bound momentarily then released; guaranteed free
    start = time.monotonic()
    with pytest.raises(ChromeDebugPortUnavailable, match="Chrome 136"):
        await check_cdp_port("127.0.0.1", dead_port, timeout=1.5, poll_interval=0.1)
    elapsed = time.monotonic() - start
    # Must actually respect the timeout (not hang indefinitely, not raise instantly
    # without having polled at all).
    assert 1.0 <= elapsed <= 5.0


@pytest.mark.asyncio
async def test_reuses_already_running_chrome_instead_of_launching_twice(tmp_path):
    settings = ChromeSettings(
        debug_port=_free_tcp_port(),
        profile_dir=str(tmp_path / "chrome-profile"),
    )
    launcher = ChromeLauncher(settings, extra_args=_SANDBOX_ONLY_ARGS)
    first = await launcher.ensure_running()
    try:
        assert first.launched_by_us is True
        second = await launcher.ensure_running()
        assert second.launched_by_us is False
        assert second.port == first.port
        assert second.process is None
    finally:
        launcher.shutdown(first)
        if first.process is not None:
            first.process.terminate()
            try:
                first.process.wait(timeout=5)
            except Exception:
                first.process.kill()
