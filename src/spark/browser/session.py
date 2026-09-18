"""Playwright attachment to an already-launched, CDP-debuggable Chrome.

See BUILD_SPEC.md §6.2. Deliberately does NOT use ``chromium.launch()`` —
Spark always attaches over CDP to a real Chrome instance so the user's
signed-in session on the automation profile (BUILD_SPEC §6.11) carries over
between runs. Creating a fresh browser context here would silently discard
that login.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from playwright.async_api import Browser, BrowserContext, Frame, Page, async_playwright

from spark.logsetup import get_logger

log = get_logger("browser.session")


class BrowserSession:
    """Wraps one Playwright ``Page`` on a CDP-attached browser, plus the
    settle-detection and frame access the rest of Spark needs.
    """

    def __init__(
        self,
        playwright,
        browser: Browser,
        context: BrowserContext,
        page: Page,
    ) -> None:
        self._playwright = playwright
        self.browser = browser
        self.context = context
        self.page = page
        self._last_network_activity = time.monotonic()
        self._attach_network_tracking(page)

    # -- construction ----------------------------------------------------

    @classmethod
    async def attach(cls, cdp_url: str, prefer_url_substring: str | None = None) -> "BrowserSession":
        playwright = await async_playwright().start()
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
        except Exception:
            await playwright.stop()
            raise

        if not browser.contexts:
            # A Chrome launched with --no-first-run should already have a
            # default context. If it genuinely has none, create one — but
            # loudly, since a brand new context has no cookies and defeats
            # the point of attaching to a persistent profile.
            log.warning(
                "CDP-attached browser reported no existing context; creating "
                "a new one. This loses any signed-in session on the profile."
            )
            context = await browser.new_context()
        else:
            context = browser.contexts[0]

        page = cls._select_page(context, prefer_url_substring)
        if page is None:
            page = await context.new_page()

        return cls(playwright, browser, context, page)

    @staticmethod
    def _select_page(context: BrowserContext, prefer_url_substring: str | None) -> Page | None:
        pages = context.pages
        if not pages:
            return None
        if prefer_url_substring:
            for p in pages:
                if prefer_url_substring in p.url:
                    return p
        for p in pages:
            if p.url not in ("about:blank", ""):
                return p
        return pages[0]

    def _attach_network_tracking(self, page: Page) -> None:
        def _touch(_arg: object = None) -> None:
            self._last_network_activity = time.monotonic()

        page.on("request", _touch)
        page.on("requestfinished", _touch)
        page.on("requestfailed", _touch)

    async def use_page(self, page: Page) -> None:
        """Switch the active page (e.g. the flow opened a new tab) and
        re-attach network tracking to it.
        """
        self.page = page
        self._last_network_activity = time.monotonic()
        self._attach_network_tracking(page)

    async def close(self) -> None:
        """Disconnect Playwright's CDP session only. Never terminates the
        underlying Chrome process — that is the launcher's responsibility,
        and its default is to leave Chrome running (BUILD_SPEC §6.1).
        """
        await self._playwright.stop()

    # -- navigation & inspection ------------------------------------------

    @property
    def current_url(self) -> str:
        return self.page.url

    async def goto(self, url: str, *, wait_until: str = "load", timeout_ms: int = 30_000) -> None:
        await self.page.goto(url, wait_until=wait_until, timeout=timeout_ms)

    async def evaluate(self, expression: str, arg: object = None):
        return await self.page.evaluate(expression, arg)

    async def screenshot(self, path: str | None = None, *, full_page: bool = True) -> bytes:
        return await self.page.screenshot(path=path, full_page=full_page)

    def frames(self) -> list[Frame]:
        """All frames on the current page, main frame first. DOM extraction
        (perception/dom.py) walks these to find content inside iframes.
        """
        return self.page.frames

    async def wait_for_settle(self, *, quiet_ms: int = 500, max_wait_s: float = 10.0) -> None:
        """Wait for the page to reach a stable state without relying on
        Playwright's ``networkidle`` — many single-page apps keep long-lived
        connections open and never reach it.

        Strategy (BUILD_SPEC §6.2): wait for ``load``, then poll until both
        network activity and DOM mutations have been quiet for ``quiet_ms``,
        capped at ``max_wait_s`` total. Reaching the cap is NOT an error —
        it just means "stop waiting"; the caller proceeds with whatever
        state exists.
        """
        try:
            await self.page.wait_for_load_state("load", timeout=max_wait_s * 1000)
        except Exception:
            pass  # already past load, or a slow/never-settling SPA — proceed regardless

        mutation_probe = """
        () => {
            if (!window.__sparkMutation) {
                window.__sparkMutation = { last: Date.now() };
                const obs = new MutationObserver(() => { window.__sparkMutation.last = Date.now(); });
                obs.observe(document.documentElement, {
                    childList: true, subtree: true, attributes: true, characterData: true
                });
            }
            return Date.now() - window.__sparkMutation.last;
        }
        """
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            network_quiet_ms = (time.monotonic() - self._last_network_activity) * 1000
            try:
                dom_quiet_ms = await self.page.evaluate(mutation_probe)
            except Exception:
                return  # navigation destroyed the execution context; treat as settled
            if network_quiet_ms >= quiet_ms and dom_quiet_ms >= quiet_ms:
                return
            await asyncio.sleep(0.1)
        log.debug(
            "wait_for_settle: reached max_wait_s=%.1f without full quiescence; proceeding anyway",
            max_wait_s,
        )
