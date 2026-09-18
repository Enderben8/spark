"""Action-execution primitives: click, type, select, scroll, navigate.

See BUILD_SPEC.md §7. Execution rules that matter:

* Resolve ``element_id`` against the CURRENT PageView's inventory only —
  never a stale one (the orchestrator re-perceives after every mutating
  action, BUILD_SPEC §8).
* Scroll an element into view before interacting with it (an off-viewport
  element is a common silent failure) — every function here does this
  itself via Playwright's ``scroll_into_view_if_needed``.
* A click/type that doesn't visibly change anything is a failure to be
  retried differently, not repeated identically — this module raises
  :class:`ActionExecutionError` so the orchestrator can decide the retry
  strategy (try the label instead of the input, fall back to a coordinate
  click, etc.) rather than silently swallowing the failure here.
"""
from __future__ import annotations

from playwright.async_api import Frame, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from spark.logsetup import get_logger
from spark.perception.dom import InteractiveElement, resolve_frame

log = get_logger("browser.actions")

DEFAULT_ACTION_TIMEOUT_MS = 5000


class ActionExecutionError(RuntimeError):
    pass


class ElementNotFoundError(ActionExecutionError):
    pass


def _get_frame(frames: list[Frame], element: InteractiveElement) -> Frame:
    frame = resolve_frame(frames, element.frame_path)
    if frame is None:
        raise ElementNotFoundError(
            f"Could not resolve frame '{element.frame_path}' for element {element.id} "
            f"({element.tag} '{element.name}') — the page structure likely changed "
            f"since this PageView was captured; re-perceive before retrying."
        )
    return frame


async def _resolve(frames: list[Frame], element: InteractiveElement):
    """The locator for ``element``: its recorded selector if that matches
    exactly one thing, else a backup found by role + visible name. Playwright
    refuses to act on an ambiguous locator (strict mode), which is right — it
    must never click a guess — so we only fall back when the alternative is
    itself unambiguous, and otherwise raise a clear error.
    """
    frame = _get_frame(frames, element)
    primary = frame.locator(element.selector)
    count = await primary.count()
    if count == 1:
        return primary
    if element.name and element.role not in ("generic", ""):
        try:
            by_role = frame.get_by_role(element.role, name=element.name, exact=True)  # type: ignore[arg-type]
            if await by_role.count() == 1:
                log.info("Selector for %s matched %d elements; using role+name instead", element.id, count)
                return by_role
        except Exception:
            pass
    raise ElementNotFoundError(
        f"Element {element.id} ({element.tag} '{element.name}') is ambiguous or missing: its "
        f"selector {element.selector!r} matched {count} elements and no unique role+name match exists"
    )


async def click_element(
    frames: list[Frame],
    element: InteractiveElement,
    *,
    timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS,
) -> None:
    locator = await _resolve(frames, element)
    try:
        await locator.scroll_into_view_if_needed(timeout=timeout_ms)
        await locator.click(timeout=timeout_ms)
    except PlaywrightTimeoutError as exc:
        raise ActionExecutionError(
            f"Timed out clicking element {element.id} ({element.tag} '{element.name}', "
            f"selector '{element.selector}'): {exc}"
        ) from exc
    except Exception as exc:
        raise ActionExecutionError(
            f"Failed to click element {element.id} ({element.tag} '{element.name}'): {exc}"
        ) from exc


async def click_at_coordinates(page: Page, x: float, y: float) -> None:
    """Click at VIEWPORT-relative coordinates — used only for content with
    no DOM representation to select (canvas-rendered controls located via a
    geometry-capable OCR engine, BUILD_SPEC §6.4). The caller is
    responsible for ensuring ``(x, y)`` is currently within the viewport
    (e.g. by scrolling to the tile the coordinates came from) and for
    converting from the OCR image's pixel space via that tile's recorded
    scroll offset and device pixel ratio — this function does no coordinate
    math of its own.
    """
    await page.mouse.click(x, y)


async def type_text(
    frames: list[Frame],
    element: InteractiveElement,
    text: str,
    *,
    clear_first: bool = True,
    timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS,
) -> None:
    locator = await _resolve(frames, element)
    try:
        await locator.scroll_into_view_if_needed(timeout=timeout_ms)
        if clear_first:
            await locator.fill(text, timeout=timeout_ms)
        else:
            await locator.press_sequentially(text, timeout=timeout_ms)
    except Exception as exc:
        raise ActionExecutionError(
            f"Failed to type into element {element.id} ({element.tag} '{element.name}'): {exc}"
        ) from exc


async def select_option(
    frames: list[Frame],
    element: InteractiveElement,
    value: str,
    *,
    timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS,
) -> None:
    locator = await _resolve(frames, element)
    try:
        await locator.scroll_into_view_if_needed(timeout=timeout_ms)
        await locator.select_option(value, timeout=timeout_ms)
    except Exception as exc:
        raise ActionExecutionError(
            f"Failed to select '{value}' on element {element.id} ({element.tag} '{element.name}'): {exc}"
        ) from exc


async def scroll_page(page: Page, *, direction: str, amount_px: int = 800) -> None:
    if direction == "down":
        await page.evaluate("(amount) => window.scrollBy(0, amount)", amount_px)
    elif direction == "up":
        await page.evaluate("(amount) => window.scrollBy(0, -amount)", amount_px)
    else:
        raise ActionExecutionError(
            f"scroll_page: unsupported direction '{direction}' (use scroll_to_element for 'to_element')"
        )


async def scroll_to_element(
    frames: list[Frame],
    element: InteractiveElement,
    *,
    timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS,
) -> None:
    locator = await _resolve(frames, element)
    try:
        await locator.scroll_into_view_if_needed(timeout=timeout_ms)
    except Exception as exc:
        raise ActionExecutionError(f"Failed to scroll to element {element.id}: {exc}") from exc


async def navigate(session, url: str) -> None:
    await session.goto(url)


async def verify_checked(
    frames: list[Frame],
    element: InteractiveElement,
    *,
    timeout_ms: int = DEFAULT_ACTION_TIMEOUT_MS,
) -> bool:
    """Re-read the live ``checked`` state of a radio/checkbox after clicking
    it (BUILD_SPEC §6.9 step 4: "verify the selection stuck ... An
    unverified click is a failed click.").
    """
    locator = await _resolve(frames, element)
    try:
        return await locator.is_checked(timeout=timeout_ms)
    except Exception:
        return False
