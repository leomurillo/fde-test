"""Browser smoke tests (Chromium via Playwright, fake microphone).

Skipped automatically when the playwright package or a Chromium build is not available, so a plain
`pytest` on a fresh clone still passes. To run: `pip install -r requirements-dev.txt && playwright install chromium`.
"""

import json
import os
import socket
import sqlite3
import threading
import time
import wave
from pathlib import Path

import httpx
import pytest

sync_api = pytest.importorskip("playwright.sync_api", exc_type=ImportError)

if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH") and Path("/opt/pw-browsers").is_dir():
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/opt/pw-browsers"

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SCOPE = ["agents.when", "agents.patterns", "agents.state", "agents.multi"]
ALL = ["single", "multi", "numeric", "open", "audio", "sql", "code"]


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        except Exception as exc:  # no Chromium build installed
            pytest.skip(f"Chromium not available: {str(exc).splitlines()[0]}")
        yield b
        b.close()


@pytest.fixture
def server(tmp_path):
    import uvicorn

    from app.main import create_app

    app = create_app(data_dir=tmp_path / "var", bank_dir=FIXTURES)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", app
    srv.should_exit = True
    t.join(timeout=5)


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, permissions=["microphone"])
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    yield pg
    ctx.close()


def new_session(base, mode, formats=ALL, length=7):
    r = httpx.post(f"{base}/new", data={"subtopics": SCOPE, "formats": formats, "length": str(length), "mode": mode,
                                        "priority": "unseen_first", "seed": "7"}, follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[-1]


def db(app):
    c = sqlite3.connect(app.state.ctx.db_path)
    c.row_factory = sqlite3.Row
    return c


def answer_current_item(pg, mode):
    t = pg.get_attribute("#item", "data-type")
    if t in ("single", "multi"):
        pg.keyboard.press("1")
        if t == "multi":
            pg.keyboard.press("2")
        pg.keyboard.press("f")
        assert pg.get_attribute("#flag-btn", "aria-pressed") == "true"
        pg.keyboard.press("Enter")
    elif t == "numeric":
        pg.fill("#numeric-input", "5.25")
        pg.keyboard.press("Enter")
    elif t == "open":
        pg.fill("#open-input", "Recommend a deterministic workflow with two model steps.")
        pg.click("#submit-btn")
    elif t == "sql":
        pg.fill("#code-input", "SELECT run_id, SUM(input_tokens + output_tokens) AS total_tokens FROM calls "
                               "GROUP BY run_id HAVING total_tokens > 1000")
        pg.click("#run-btn")
        pg.wait_for_selector("#run-output table")
        pg.click("#submit-btn")
    elif t == "code":
        pg.fill("#code-input", "def backoff_delays(attempts, base, cap):\n"
                               "    return [min(cap, base * 2 ** i) for i in range(max(0, attempts))]\n")
        pg.click("#run-btn")
        pg.wait_for_selector("#run-output .case-pass")
        pg.click("#submit-btn")
    elif t == "audio":
        pg.click("#audio-begin")
        pg.wait_for_selector("[data-stage=prep]:not([hidden])")
        pg.click("#audio-start-now")
        pg.wait_for_selector("[data-stage=rec]:not([hidden])")
        time.sleep(1.5)
        pg.click("#audio-stop")
    return t


@pytest.mark.parametrize("mode", ["realtime", "end"])
def test_full_session_with_wav_recording(server, page, mode):
    base, app = server
    sid = new_session(base, mode)
    page.goto(f"{base}/s/{sid}")
    types = []
    for _ in range(7):
        page.wait_for_selector("#item")
        url = page.url
        types.append(answer_current_item(page, mode))
        if mode == "realtime":
            page.wait_for_selector("#reveal .reveal", timeout=30000)
            page.keyboard.press("Enter")
        page.wait_for_url(lambda u: u != url, timeout=30000)
    assert sorted(types) == sorted(ALL)
    page.wait_for_url("**/end")
    page.click("#finish-btn")
    page.wait_for_url("**/review")
    assert page.locator(".review-item").count() == 7

    with db(app) as c:
        rows = c.execute("SELECT item_id, response_json, skipped FROM responses WHERE session_id=?", (sid,)).fetchall()
    assert len(rows) == 7 and not any(r["skipped"] for r in rows)
    audio = json.loads(next(r for r in rows if r["item_id"] == "D6-905")["response_json"])
    assert audio["mime"] == "audio/wav" and audio["audio"].endswith(".wav"), audio
    assert "encode_error" not in audio and audio["attempt_started_at"]
    path = app.state.ctx.data_dir / audio["audio"]
    assert path.read_bytes()[:4] == b"RIFF"
    with wave.open(str(path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        assert w.getnframes() > 16000 * 0.5
    assert page.errors == []


def test_reload_after_prep_forces_written_fallback(server, page):
    base, app = server
    sid = new_session(base, "realtime", formats=["audio"], length=1)
    page.goto(f"{base}/s/{sid}/item/1")
    page.click("#audio-begin")
    page.wait_for_selector("[data-stage=prep]:not([hidden])")
    page.reload()
    page.wait_for_selector("#attempt-used")
    assert page.locator("#audio-begin").count() == 0
    page.click("#audio-text-used")
    page.wait_for_selector("#fallback-input:visible")
    page.type("#fallback-input", "Not yet; measure first.")
    page.keyboard.press("Backspace")  # no-edit: ignored
    page.click("#audio-stop")
    page.wait_for_selector("#reveal .reveal", timeout=30000)
    with db(app) as c:
        ans = json.loads(c.execute("SELECT response_json FROM responses").fetchone()[0])
    assert ans["attempt_used"] is True and ans["fallback"] == "text" and ans["text"] == "Not yet; measure first."
    assert page.errors == []


def test_progress_domains_load_lazily(server, page):
    base, _ = server
    page.goto(f"{base}/progress")
    assert page.locator(".browser-item").count() == 0
    page.click("#d-D6 > summary")
    page.wait_for_selector("#d-D6 .browser-item")
    assert page.locator("#d-D6 .browser-item").count() == 7
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert page.errors == []


def test_end_test_menu_abandons_from_runner_and_dashboard(server, page):
    base, app = server
    sid = new_session(base, "realtime", formats=["single"], length=3)
    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.accept()))

    page.goto(f"{base}/s/{sid}/item/1")
    page.click(".end-menu > summary")
    assert page.is_visible("text=Finish and see results") and page.is_visible("text=Abandon test")
    page.keyboard.press("Escape")
    assert not page.is_visible("text=Abandon test")
    page.click(".end-menu > summary")
    page.focus(".end-menu > summary")
    page.keyboard.press("Enter")  # toggles the menu; must not trigger the runner's own Enter shortcut
    assert not page.is_visible("text=Abandon test") and page.url.endswith("/item/1")
    page.keyboard.press("Enter")
    page.click("text=Abandon test")
    page.wait_for_url(f"{base}/")
    assert "still count in Progress" in messages[-1]
    assert page.is_visible(".session-list .badge:has-text('Abandoned')") and page.locator("text=Resume test").count() == 0

    sid2 = new_session(base, "realtime", formats=["single"], length=3)
    page.goto(f"{base}/")
    assert page.is_visible("text=Resume test")
    page.click(".resume-card >> text=Abandon")
    page.wait_for_load_state()
    assert page.locator("text=Resume test").count() == 0
    with db(app) as c:
        assert {r[0]: r[1] for r in c.execute("SELECT session_id, status FROM sessions")} == {
            sid: "abandoned", sid2: "abandoned"}
    assert page.errors == []
