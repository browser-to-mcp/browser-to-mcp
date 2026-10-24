"""Shared decisions and real hostile-page checks on local servers only."""

from __future__ import annotations

import asyncio
import html
import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest

from browser_to_mcp.authoring.browser import SelectionSession
from browser_to_mcp.authoring.plan import AuthoringError
from browser_to_mcp.browser_policy import BrowserBoundary, request_violation
from browser_to_mcp.loader import LoadedWorkflow, load_workflow, manifest_digest
from browser_to_mcp.models import WorkflowStep
from browser_to_mcp.recorder.session import RecordingSession
from browser_to_mcp.replay import (
    ExecutionFailureCategory,
    PlaywrightReplayEngine,
    WorkflowExecutionError,
    replay_workflow,
)
from browser_to_mcp.validation import WorkflowValidationError

SOURCE = Path(__file__).parents[1] / "workflows/hn_search.yaml"


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
def test_shared_method_decision(method):
    result = request_violation("https://example.com/public", method, ["example.com"])
    assert (result.code if result else None) == (
        None if method in {"GET", "HEAD", "OPTIONS"} else "write_request"
    )


@pytest.mark.parametrize(
    "url,code",
    [
        ("https://other.example/private?token=SECRET", "blocked_request"),
        ("https://user:SECRET@example.com/", "unsafe_url"),
        ("https://example.com/?token=SECRET", "unsafe_url"),
        ("https://example.com/#secret", "unsafe_url"),
        ("https://example.com/#public-section", None),
        ("https://example.com:invalid/", "blocked_request"),
        ("https://[broken/", "blocked_request"),
        ("file:///private/SECRET", "blocked_request"),
        ("data:text/html,SECRET", "blocked_request"),
        ("blob:https://example.com/SECRET", "blocked_request"),
    ],
)
def test_shared_url_decisions_never_include_private_url(url, code):
    violation = request_violation(url, "GET", ["example.com"])
    assert (violation.code if violation else None) == code
    if violation:
        assert "SECRET" not in violation.message
        assert url not in violation.message


def test_hosted_execution_refused_before_creating_browser_context():
    browser = AsyncMock()
    with pytest.raises(ValueError, match="Only local"):
        asyncio.run(BrowserBoundary(["127.0.0.1"]).create_context(browser, environment="hosted"))
    browser.new_context.assert_not_called()


def step(step_id, action, **fields):
    return WorkflowStep(
        id=step_id,
        action=action,
        timeout_seconds=3,
        risk="read_only",
        idempotency="safe",
        retry="none",
        confirmation="never",
        **fields,
    )


def workflow(base, case, *, download=False):
    original = load_workflow(SOURCE).workflow.model_copy(deep=True)
    original.inputs = {}
    original.permissions.allowed_domains = ["127.0.0.1"]
    original.permissions.downloads = download
    original.steps = [step("open", "goto", url=f"{base}/case/{case}")]
    if download:
        original.steps.append(step("results", "download", selector="#download"))
    else:
        original.steps += [
            step("trigger", "click", selector="#trigger"),
            step("ready", "wait_for_text", text="Finished"),
            step("results", "extract_text", selector="#result"),
        ]
    return original


class Site(ThreadingHTTPServer):
    def __init__(self):
        self.hits = Counter()
        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server: Site

    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.hits[("GET", path)] += 1
        if path.startswith("/redirect"):
            self.send_response(302)
            self.send_header(
                "Location",
                {
                    "/redirect/allowed": "/destination",
                    "/redirect/denied": self.server.base.replace("127.0.0.1", "localhost")
                    + "/destination?token=PRIVATE-URL",
                    "/redirect/chain": "/redirect/denied",
                }[path],
            )
            self.end_headers()
            return
        if path == "/broken":
            self.connection.close()
            return
        content_type = "text/html; charset=utf-8"
        if path == "/attachment":
            body = b"PRIVATE-FILE"
            content_type = "application/octet-stream"
        elif path == "/worker.js":
            content_type = "application/javascript"
            body = b"fetch('/destination')"
        elif path.startswith("/case/"):
            case = path.rsplit("/", 1)[-1]
            attack = {
                "post": "fetch('/destination',{method:'POST',body:'PRIVATE-BODY'})",
                "put": "fetch('/destination',{method:'PUT',body:'PRIVATE-BODY'})",
                "options": "fetch('/destination',{method:'OPTIONS'})",
                "head": "fetch('/destination',{method:'HEAD'})",
                "get": "fetch('/destination')",
                "network": "fetch('/broken?private=PRIVATE-URL')",
                "denied": "fetch('"
                + self.server.base.replace("127.0.0.1", "localhost")
                + "/destination?token=PRIVATE-URL')",
                "secret": "fetch('/destination?token=PRIVATE-URL')",
                "redirect_allowed": "fetch('/redirect/allowed')",
                "redirect_denied": "fetch('/redirect/denied')",
                "redirect_chain": "fetch('/redirect/chain')",
                "popup": "window.open('/destination')",
                "popup_link": "document.querySelector('#popup-link').click()",
                "frame": "document.body.insertAdjacentHTML('beforeend',"
                + "'<iframe src=/destination></iframe>')",
                "srcdoc": "document.body.insertAdjacentHTML('beforeend',"
                + "'<iframe srcdoc=PRIVATE-PAGE></iframe>')",
                "service_worker": "navigator.serviceWorker.register('/worker.js')",
                "worker": "new Worker('/worker.js')",
                "shared_worker": "new SharedWorker('/worker.js')",
                "transport": "new WebTransport('https://127.0.0.1:9/PRIVATE-URL')",
                "socket": "new WebSocket('"
                + self.server.base.replace("http:", "ws:")
                + "/destination')",
                "rtc": "new RTCPeerConnection()",
                "eventsource": "new EventSource('/destination')",
                "permission": "Notification.requestPermission()",
                "clipboard": "navigator.clipboard.writeText('PRIVATE-CLIPBOARD')",
                "download": "document.querySelector('#download').click()",
                "dialog": "alert('PRIVATE-DIALOG')",
                "blank_navigation": "location.href='about:blank'",
            }[case]
            handler = (
                "try { Promise.resolve(" + attack + ").catch(()=>{}); } catch(e) {} "
                "setTimeout(()=>document.querySelector('#result').textContent='Finished',100)"
            )
            body = (
                '<!doctype html><meta charset=utf-8><button id=trigger onclick="'
                + html.escape(handler, quote=True)
                + '">Try</button>'
                + "<output id=result></output>"
                + "<a id=download href=/attachment download>Download</a>"
                + '<a id=popup-link href=/destination target=_blank>Open new tab</a>'
            ).encode()

        else:
            body = b"<main>Public result</main>"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if path == "/attachment":
            self.send_header("Content-Disposition", 'attachment; filename="result.txt"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self._empty()

    def do_OPTIONS(self):
        self._empty()

    def do_POST(self):
        self._empty()

    def do_PUT(self):
        self._empty()

    def _empty(self):
        self.server.hits[(self.command, urlsplit(self.path).path)] += 1
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_):
        pass


@pytest.fixture
def site():
    server = Site()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


CASES = [
    ("post", "write_request"),
    ("put", "write_request"),
    ("denied", "blocked_request"),
    ("secret", "unsafe_url"),
    ("redirect_allowed", "redirect"),
    ("redirect_denied", "redirect"),
    ("redirect_chain", "redirect"),
    ("popup", "duplicate_tab"),
    ("popup_link", "duplicate_tab"),
    ("frame", "frame"),
    ("srcdoc", "frame"),
    ("service_worker", "service_worker"),
    ("worker", "worker"),
    ("shared_worker", "worker"),
    ("transport", "socket"),
    ("socket", "websocket"),
    ("rtc", "socket"),
    ("eventsource", "socket"),
    ("permission", "permission_request"),
    ("clipboard", "permission_request"),
    ("download", "file_transfer"),
    ("dialog", "dialog"),
    ("blank_navigation", "blocked_navigation"),
]


@pytest.mark.parametrize("entry", ["record", "preview", "replay"])
@pytest.mark.parametrize("case,code", CASES)
def test_hostile_action_has_same_refusal_at_every_entry(site, entry, case, code, tmp_path):
    async def exercise():
        contract = workflow(site.base, case)
        if entry == "replay":
            loaded = LoadedWorkflow(contract, manifest_digest(contract))
            with pytest.raises(WorkflowExecutionError) as caught:
                await replay_workflow(loaded, [], tmp_path)
            assert caught.value.category is ExecutionFailureCategory.PERMISSION
            assert caught.value.message.startswith(code + ":")
            trace = json.loads(Path(caught.value.trace_path).read_text())
            assert trace["outputs"] == {} and trace["screenshots"] == []
            diagnostic = Path(caught.value.trace_path).read_text()
        else:
            cls = RecordingSession if entry == "record" else SelectionSession
            async with cls(site.base + "/case/" + case, ["127.0.0.1"], headless=True) as session:
                if entry == "preview":
                    with pytest.raises((WorkflowExecutionError, AuthoringError)):
                        await session.prepare([s.model_dump(mode="json") for s in contract.steps])
                else:
                    await session.page.locator("#trigger").click()
                for _ in range(200):
                    if session.boundary.failure:
                        break
                    await asyncio.sleep(0.01)
                assert session.boundary.failure.code == code
                draft = await session.finish()
                assert draft.status == "partial"
                diagnostic = draft.model_dump_json()
            assert session.context is None and session.browser is None
        assert not list(tmp_path.glob("*.png"))
        for secret in (
            "PRIVATE-URL",
            "PRIVATE-BODY",
            "PRIVATE-PAGE",
            "PRIVATE-DIALOG",
            "PRIVATE-FILE",
            "PRIVATE-CLIPBOARD",
        ):
            assert secret not in diagnostic
        if case != "download":
            assert site.hits[("GET", "/destination")] == 0
        assert site.hits[("POST", "/destination")] == 0
        assert site.hits[("PUT", "/destination")] == 0
        assert site.hits[("GET", "/worker.js")] == 0
        if case == "redirect_chain":
            assert site.hits[("GET", "/redirect/denied")] == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("case,method", [("get", "GET"), ("head", "HEAD"), ("options", "OPTIONS")])
@pytest.mark.parametrize("entry", ["record", "preview", "replay"])
def test_allowed_methods_and_explicit_loopback_still_work(site, case, method, entry):
    async def exercise():
        contract = workflow(site.base, case)
        if entry == "replay":
            result = await PlaywrightReplayEngine().run(contract, {})
            assert result.execution.outputs == {"results": "Finished"}
        else:
            cls = RecordingSession if entry == "record" else SelectionSession
            async with cls(site.base + "/case/" + case, ["127.0.0.1"], headless=True) as session:
                if entry == "preview":
                    await session.prepare([s.model_dump(mode="json") for s in contract.steps])
                else:
                    await session.page.locator("#trigger").click()
                    await session.page.get_by_text("Finished").wait_for()
                assert session.boundary.failure is None
        assert site.hits[(method, "/destination")] == 1

    asyncio.run(exercise())


def test_declared_download_returns_filename_without_retaining_bytes(site, tmp_path):
    async def exercise():
        result = await PlaywrightReplayEngine().run(
            workflow(site.base, "download", download=True), {}
        )
        assert result.execution.outputs == {"results": "result.txt"}

    asyncio.run(exercise())
    assert site.hits[("GET", "/attachment")] == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "url", ["https://u:PRIVATE@example.com/", "https://example.com/?token=PRIVATE"]
)
def test_unsafe_workflow_navigation_fails_before_browser_start(url):
    contract = load_workflow(SOURCE).workflow.model_copy(deep=True)
    contract.permissions.allowed_domains = ["example.com"]
    contract.steps[0].url = url
    factory = AsyncMock()
    with pytest.raises(WorkflowValidationError) as caught:
        asyncio.run(PlaywrightReplayEngine(playwright_factory=factory).run(contract, {}))
    factory.assert_not_called()
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("entry", ["record", "preview", "replay"])
def test_initial_redirect_is_refused_before_any_destination_request(site, entry):
    async def exercise():
        url = site.base + "/redirect/chain"
        if entry == "replay":
            contract = workflow(site.base, "get")
            contract.steps[0].url = url
            with pytest.raises(WorkflowExecutionError) as caught:
                await PlaywrightReplayEngine().run(contract, {})
            assert caught.value.category is ExecutionFailureCategory.PERMISSION
            assert caught.value.message.startswith("redirect:")
        else:
            cls = RecordingSession if entry == "record" else SelectionSession
            async with cls(url, ["127.0.0.1"], headless=True) as session:
                assert session.boundary.failure.code == "redirect"
                assert (await session.finish()).status == "partial"
            assert session.browser is None and session.context is None
        assert site.hits[("GET", "/redirect/chain")] == 1
        assert site.hits[("GET", "/redirect/denied")] == 0
        assert site.hits[("GET", "/destination")] == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("entry", ["cli", "mcp"])
def test_real_public_entry_returns_permission_and_private_trace(site, tmp_path, capsys, entry):
    import os
    import sys

    import yaml
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from browser_to_mcp.cli import run

    contract = workflow(site.base, "post")
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(contract.model_dump(mode="json", exclude_none=True)))
    traces = tmp_path / "traces"
    if entry == "cli":
        assert run(["replay", str(path), "--trace-dir", str(traces)]) == 1
        output = capsys.readouterr()
        response = json.loads(output.err)
    else:

        async def invoke():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-c",
                    "from browser_to_mcp.cli import main; main()",
                    "server",
                    "--workflow-dir",
                    str(tmp_path),
                    "--trace-dir",
                    str(traces),
                ],
                env={k: os.environ[k] for k in ["PLAYWRIGHT_BROWSERS_PATH"] if k in os.environ},
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool("hn_search", {})

        result = asyncio.run(invoke())
        assert result.isError
        response = result.structuredContent
    assert response["category"] == "permission"
    assert response["message"].startswith("write_request:")
    trace = json.loads(Path(response["trace_path"]).read_text())
    assert trace["status"] == "failed" and trace["outputs"] == {}
    assert trace["screenshots"] == []
    assert not list(traces.glob("*.png"))
    assert "PRIVATE" not in json.dumps(trace)
    assert site.hits[("POST", "/destination")] == 0


def test_network_failure_is_runtime_and_omits_private_details(site, tmp_path):
    async def exercise():
        contract = workflow(site.base, "network")
        loaded = LoadedWorkflow(contract, manifest_digest(contract))
        with pytest.raises(WorkflowExecutionError) as caught:
            await replay_workflow(loaded, [], tmp_path)
        assert caught.value.category is ExecutionFailureCategory.RUNTIME
        assert caught.value.message.startswith("network_error:")
        trace = Path(caught.value.trace_path).read_text()
        assert "PRIVATE-URL" not in trace and site.base not in trace
        assert json.loads(trace)["screenshots"] == []
        assert site.hits[("GET", "/broken")] == 1  # no automatic network retry

    asyncio.run(exercise())
