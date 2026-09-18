"""M3 acceptance tests (BUILD_SPEC.md milestone table): full text and a
correctly-named Next button are reported on the fixture passage page; the
iframe variant works; hidden content is excluded; unlabelled radios pick up
their option text from a sibling via nearby_text.
"""
from __future__ import annotations

import pytest

from spark.browser.launcher import ChromeLauncher
from spark.browser.session import BrowserSession
from spark.config import ChromeSettings
from spark.perception.dom import extract

_SANDBOX_ONLY_ARGS = ["--no-sandbox", "--headless=new"]


@pytest.fixture
async def session(tmp_path):
    settings = ChromeSettings(profile_dir=str(tmp_path / "chrome-profile"), debug_port=0)
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        settings.debug_port = s.getsockname()[1]

    launcher = ChromeLauncher(settings, extra_args=_SANDBOX_ONLY_ARGS)
    result = await launcher.ensure_running()
    sess = await BrowserSession.attach(result.cdp_url)
    try:
        yield sess
    finally:
        await sess.close()
        if result.process is not None:
            result.process.terminate()
            try:
                result.process.wait(timeout=5)
            except Exception:
                result.process.kill()


PASSAGE_HTML = """
<html><body>
<h1 style="display:none">Hidden Title</h1>
<p>The quick brown fox jumps over the lazy dog.</p>
<p>Ferns reproduce by spores, not seeds.</p>
<div style="visibility:hidden"><p>This should never appear.</p></div>
<div aria-hidden="true"><p>Neither should this.</p></div>
<button id="next-btn">Next</button>
</body></html>
"""


@pytest.mark.asyncio
async def test_extracts_visible_text_and_named_button(session):
    await session.goto("data:text/html," + PASSAGE_HTML.replace("#", "%23"))
    result = await extract(session.frames())

    assert "quick brown fox" in result.text
    assert "Ferns reproduce" in result.text
    assert "Hidden Title" not in result.text
    assert "should never appear" not in result.text
    assert "Neither should this" not in result.text

    next_buttons = [e for e in result.elements if e.name.strip().lower() == "next"]
    assert len(next_buttons) == 1
    assert next_buttons[0].tag == "button"
    assert next_buttons[0].role == "button"


RADIO_HTML = """
<html><body>
<p>What color is the sky?</p>
<div class="option"><input type="radio" name="q1" id="opt1"><span>Blue</span></div>
<div class="option"><input type="radio" name="q1" id="opt2"><span>Green</span></div>
</body></html>
"""


@pytest.mark.asyncio
async def test_unlabelled_radio_picks_up_sibling_text(session):
    await session.goto("data:text/html," + RADIO_HTML)
    result = await extract(session.frames())

    radios = [e for e in result.elements if e.role == "radio"]
    assert len(radios) == 2
    texts = {r.nearby_text for r in radios}
    assert "Blue" in texts
    assert "Green" in texts
    assert all(r.state.checked is False for r in radios)


CANVAS_HTML = """
<html><body style="margin:0">
<canvas id="c" width="800" height="2000"></canvas>
<script>
const ctx = document.getElementById('c').getContext('2d');
ctx.font = '20px sans-serif';
ctx.fillText('This text lives only in pixels', 10, 50);
</script>
</body></html>
"""


@pytest.mark.asyncio
async def test_canvas_page_has_no_dom_text_and_high_media_ratio(session):
    await session.goto("data:text/html," + CANVAS_HTML)
    result = await extract(session.frames())

    assert "lives only in pixels" not in result.text
    assert result.media_dominance_ratio > 0.3


IFRAME_PARENT_HTML = """
<html><body>
<p>Parent frame text.</p>
<iframe src="data:text/html,%3Cbody%3E%3Cp%3EChild%20frame%20text%20lives%20here%3C%2Fp%3E%3Cbutton%3EInner%20Button%3C%2Fbutton%3E%3C%2Fbody%3E"></iframe>
</body></html>
"""


@pytest.mark.asyncio
async def test_walks_into_iframes(session):
    await session.goto("data:text/html," + IFRAME_PARENT_HTML)
    await session.wait_for_settle(max_wait_s=3.0)
    result = await extract(session.frames())

    assert "Parent frame text" in result.text
    assert "Child frame text lives here" in result.text

    inner_button = [e for e in result.elements if e.name == "Inner Button"]
    assert len(inner_button) == 1
    assert inner_button[0].frame_path != "main"


@pytest.mark.asyncio
async def test_element_ids_are_unique_and_stable_ordering(session):
    await session.goto("data:text/html," + RADIO_HTML)
    result = await extract(session.frames())
    ids = [e.id for e in result.elements]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids, key=lambda s: int(s[1:]))
