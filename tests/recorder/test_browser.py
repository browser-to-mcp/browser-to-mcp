"""Real Chromium capture and replay: no synthetic event-only substitute."""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from browser_to_mcp.loader import load_workflow
from browser_to_mcp.models import WorkflowStep
from browser_to_mcp.recorder.draft import Click, Fill, Navigation, Press, save_draft
from browser_to_mcp.recorder.session import RecordingSession
from browser_to_mcp.replay import execute_steps

HTML = """<!doctype html><meta charset=utf-8>
<label>Find a book <input id=search autocomplete=off></label>
<input aria-label="Other input">
<button data-testid=count>Count</button><output id=count>0</output>
<button>Named action</button><input placeholder="Book title">
<label>Wrapped label <input></label>
<a id=next href=/next>Next book</a>
<input type=password id=password><input name=api_token>
<select id=choice><option>A</option><option>B</option></select>
<button id=popup onclick="window.open('/next')">Popup</button>
<a id=outside href="http://localhost:9/outside?token=network-secret">Outside</a>
<a id=download download href=/file>Download</a><input id=file type=file>
<div id=frame></div><pre id=keys></pre>
<script>
window.count = 0;
document.querySelector('[data-testid=count]').onclick = () => {
  document.querySelector('output').textContent = ++window.count;
};
document.querySelector('#search').onkeydown = event => {
  if(event.key === 'Enter') document.querySelector('#keys').textContent += 'search;';
};
document.querySelector('[aria-label]').onkeydown = event => {
  if(event.key === 'Enter') document.querySelector('#keys').textContent += 'other;';
};
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_real_capture_order_typing_and_replay_targets(site, tmp_path):
    async def exercise():
        async with RecordingSession(site, ["127.0.0.1"], headless=True) as session:
            page = session.page
            assert await session.context.cookies() == []
            await page.locator("#search").click()
            await page.locator("#search").press_sequentially("books")
            await page.locator("#search").press("Enter")
            await page.get_by_test_id("count").click()
            await page.get_by_test_id("count").click()
            await page.get_by_role("button", name="Named action", exact=True).click()
            await page.get_by_placeholder("Book title").fill("A Light in the Attic")
            await page.get_by_label("Wrapped label").fill("poetry")
            await page.locator("#next").click()
            await page.wait_for_url("**/next")
            draft = await session.finish()
        assert draft.status == "captured", draft.model_dump_json()
        assert [e.kind for e in draft.events] == [
            "navigation",
            "click",
            "fill",
            "press",
            "click",
            "click",
            "click",
            "fill",
            "fill",
            "click",
            "navigation",
        ]
        assert [e.value for e in draft.events if isinstance(e, Fill)] == [
            "books",
            "A Light in the Attic",
            "poetry",
        ]
        save_draft(draft, tmp_path / "capture.recording.json")
        # Replay action targets unchanged. Navigation observations verify the destination
        # reached by a preceding click; they must not cause a second navigation.
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page()
            workflow = load_workflow(
                Path(__file__).parents[2] / "workflows/hn_search.yaml"
            ).workflow
            for index, event in enumerate(draft.events):
                if isinstance(event, Navigation):
                    if index == 0:
                        await page.goto(event.url)
                    else:
                        assert page.url == event.url
                    continue
                fields = {"selector": event.target.selector}
                if isinstance(event, Fill):
                    fields["value"] = event.value
                if isinstance(event, Press):
                    # Deliberately move focus to prove saved key targets are honored.
                    await page.get_by_label("Other input").focus()
                    fields["key"] = event.key
                step = WorkflowStep(
                    id=f"step_{index}",
                    action=event.kind,
                    timeout_seconds=5,
                    risk="read_only",
                    idempotency="safe",
                    retry="none",
                    confirmation="never",
                    **fields,
                )
                await execute_steps(page, workflow.model_copy(update={"steps": [step]}), {})
                if isinstance(event, Press):
                    assert await page.locator("#keys").inner_text() == "search;"
                if index == 5:
                    assert await page.locator("output").inner_text() == "2"
            await browser.close()
        assert session.browser is None and session.context is None

    asyncio.run(exercise())


@pytest.mark.parametrize("field", ["#password", "[name=api_token]"])
def test_password_and_token_fields_stop_without_values(site, field):
    async def exercise():
        async with RecordingSession(site, ["127.0.0.1"], headless=True) as session:
            await session.page.locator(field).fill("NEVER-PERSIST-THIS")
            await asyncio.wait_for(session.ended.wait(), timeout=5)
            draft = await session.finish()
        assert draft.status == "partial"
        assert "NEVER-PERSIST-THIS" not in draft.model_dump_json()
        assert any(getattr(e, "code", "") == "sensitive_field" for e in draft.events)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "interaction,code",
    [
        ("popup", "duplicate_tab"),
        ("frame", "frame"),
        ("outside", "blocked_navigation"),
        ("dependency", "blocked_request"),
        ("select", "unsupported_input"),
        ("gesture", "unsupported_gesture"),
        ("download", "file_transfer"),
        ("permission", "permission_request"),
        ("write", "write_request"),
        ("dialog", "dialog"),
        ("secret_url", "unsafe_url"),
        ("socket", "websocket"),
    ],
)
def test_unsupported_and_blocked_interactions_are_diagnosed(site, interaction, code):
    async def exercise():
        async with RecordingSession(site, ["127.0.0.1"], headless=True) as session:
            page = session.page
            if interaction == "popup":
                await page.locator("#popup").click()
            elif interaction == "frame":
                await page.evaluate("""() => {
                    const f = document.createElement('iframe');
                    f.srcdoc = '<input id=hidden value=FRAME-PRIVATE>';
                    document.body.append(f);
                }""")
            elif interaction == "outside":
                await page.locator("#outside").click()
            elif interaction == "dependency":
                await page.evaluate("fetch('http://localhost:9/private').catch(() => {})")
            elif interaction == "select":
                await page.locator("#choice").select_option("B")
            elif interaction == "gesture":
                await page.get_by_test_id("count").dblclick()
            elif interaction == "download":
                await page.locator("#download").click()
            elif interaction == "permission":
                await page.evaluate("Notification.requestPermission().catch(() => {})")
            elif interaction == "write":
                await page.evaluate(
                    "fetch('/write', {method:'POST',body:'PRIVATE-BODY'}).catch(()=>{})"
                )
            elif interaction == "dialog":
                await page.evaluate("alert('PRIVATE-DIALOG')")
            elif interaction == "secret_url":
                await page.evaluate("fetch('/?token=PRIVATE-TOKEN').catch(()=>{})")
            elif interaction == "socket":
                await page.evaluate("new WebSocket('ws://127.0.0.1:9/private')")
            # Drain event callbacks, waiting for the specific observable result.
            for _ in range(100):
                if any(getattr(e, "code", "") == code for e in session.capture.events):
                    break
                await asyncio.sleep(0.01)
            draft = await session.finish()
        assert draft.status == "partial"
        assert any(getattr(e, "code", "") == code for e in draft.events), draft.model_dump_json()
        for secret in ["PRIVATE-BODY", "PRIVATE-DIALOG", "PRIVATE-TOKEN", "FRAME-PRIVATE"]:
            assert secret not in draft.model_dump_json()

    asyncio.run(exercise())


def test_browser_close_is_partial_and_releases_resources(site):
    async def exercise():
        async with RecordingSession(site, ["127.0.0.1"], headless=True) as session:
            await session.page.close()
            await asyncio.wait_for(session.ended.wait(), 5)
            draft = await session.finish()
        assert draft.status == "partial"
        assert any(getattr(e, "code", "") == "browser_closed" for e in draft.events)
        assert session.browser is None

    asyncio.run(exercise())


def test_keyboard_activation_does_not_duplicate_click(site):
    async def exercise():
        async with RecordingSession(site, ["127.0.0.1"], headless=True) as session:
            await session.page.get_by_test_id("count").press("Enter")
            assert await session.page.locator("output").inner_text() == "1"
            draft = await session.finish()
        assert len([e for e in draft.events if isinstance(e, Press)]) == 1
        assert not any(isinstance(e, Click) for e in draft.events)

    asyncio.run(exercise())


@pytest.mark.parametrize("key", ["Space", "Enter"])
def test_button_key_activation_records_one_action(site, key):
    async def exercise():
        async with RecordingSession(site, ["127.0.0.1"], headless=True) as session:
            await session.page.get_by_test_id("count").press(key)
            assert await session.page.locator("output").inner_text() == "1"
            draft = await session.finish()
        assert [event.kind for event in draft.events] == ["navigation", "press"]

    asyncio.run(exercise())


def test_browser_start_failure_returns_partial_without_raw_error():
    async def exercise():
        async with RecordingSession("http://127.0.0.1:1/", ["127.0.0.1"], headless=True) as session:
            assert session.ended.is_set()
            draft = await session.finish()
        assert draft.status == "partial"
        assert any(getattr(e, "code", "") == "browser_error" for e in draft.events)
        assert session.browser is None and session._playwright is None

    asyncio.run(exercise())


def test_real_terminal_stop_saves_and_cancel_discards(site, tmp_path):
    from browser_to_mcp.recorder.command import record

    async def exercise(choice):
        sessions = []

        def factory(*args, **kwargs):
            session = RecordingSession(*args, headless=True, **kwargs)
            sessions.append(session)
            return session

        async def reader():
            await sessions[0].page.locator("#search").fill("public books")
            return choice

        path = tmp_path / f"{choice}.recording.json"
        code = await record(
            site,
            ["127.0.0.1"],
            path,
            tmp_path / "workflows",
            reader=reader,
            session_factory=factory,
        )
        assert code == (0 if choice == "stop" else 1)
        assert path.exists() == (choice == "stop")
        assert sessions[0].browser is None
        if path.exists():
            assert "public books" in path.read_text()

    asyncio.run(exercise("stop"))
    asyncio.run(exercise("cancel"))


def test_ambiguous_text_and_shadow_dom_are_not_silently_captured(site):
    async def exercise():
        async with RecordingSession(site, ["127.0.0.1"], headless=True) as session:
            await session.page.evaluate("""() => {
                document.body.insertAdjacentHTML('beforeend', '<button>named ACTION</button>');
                const host = document.createElement('section');
                host.attachShadow({mode:'open'}).innerHTML = '<button id="shadow">Shadow</button>';
                document.body.append(host);
            }""")
            await session.page.get_by_role("button", name="Named action", exact=True).click()
            await session.page.locator("#shadow").click()
            draft = await session.finish()
        codes = [getattr(event, "code", "") for event in draft.events]
        assert "ambiguous_target" in codes
        assert "shadow_dom" in codes
        assert draft.status == "partial"

    asyncio.run(exercise())
