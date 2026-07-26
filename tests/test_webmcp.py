from __future__ import annotations

import asyncio
import json
from pathlib import Path

from browser_to_mcp.cli import run
from browser_to_mcp.webmcp import (
    NativeDiscoveryStatus,
    PermissionPolicyState,
    discover_page_tools,
    discover_url,
    write_discovery_snapshot,
)


class FakePage:
    def __init__(self, payload: object, url: str = "https://example.test/page") -> None:
        self.payload = payload
        self.url = url
        self.expression = ""
        self.argument: object = None

    async def evaluate(self, expression: str, arg: object = ...) -> object:
        self.expression = expression
        self.argument = arg
        return self.payload


def _supported_payload() -> dict[str, object]:
    return {
        "status": "supported",
        "page_url": "https://example.test/page",
        "page_origin": "https://example.test",
        "permission_state": "allowed",
        "tools": [
            {
                "name": "zeta.tool",
                "title": "Zeta",
                "description": "Return zeta data.",
                "input_schema": '{"type":"object","properties":{}}',
                "origin": "https://example.test",
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "alpha_tool",
                "title": None,
                "description": "Return alpha data.",
                "input_schema": {"type": "object", "required": []},
                "origin": "https://partner.test",
                "annotations": None,
            },
        ],
        "diagnostics": [],
    }


def test_discovery_returns_stable_sorted_metadata_without_invocation() -> None:
    first_page = FakePage(_supported_payload())
    second_page = FakePage(_supported_payload())

    first = asyncio.run(discover_page_tools(first_page, from_origins=["https://partner.test"]))
    second = asyncio.run(discover_page_tools(second_page, from_origins=["https://partner.test"]))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.status is NativeDiscoveryStatus.SUPPORTED
    assert first.permissions_policy.state is PermissionPolicyState.ALLOWED
    assert [tool.name for tool in first.tools] == ["alpha_tool", "zeta.tool"]
    assert first.tools[0].input_schema == {"type": "object", "required": []}
    assert first.tools[1].annotations == {"readOnlyHint": True}
    assert first.diagnostics == []
    assert first_page.argument == {"from_origins": ["https://partner.test"]}
    assert "getTools" in first_page.expression
    assert "executeTool" not in first_page.expression


def test_discovery_supports_legacy_navigator_model_context_surface() -> None:
    page = FakePage(_supported_payload())

    snapshot = asyncio.run(discover_page_tools(page))

    assert snapshot.status is NativeDiscoveryStatus.SUPPORTED
    assert 'globalThis.navigator?.modelContext' in page.expression
    assert '`${modelContextPath}.getTools`' in page.expression


def test_discovery_handles_a_page_with_no_native_tools() -> None:
    page = FakePage(
        {
            "status": "supported",
            "page_url": "https://example.test/empty",
            "page_origin": "https://example.test",
            "permission_state": "allowed",
            "tools": [],
            "diagnostics": [],
        },
        url="https://example.test/empty",
    )

    snapshot = asyncio.run(discover_page_tools(page))

    assert snapshot.status is NativeDiscoveryStatus.SUPPORTED
    assert snapshot.tools == []
    assert snapshot.diagnostics == []


def test_discovery_reports_unsupported_browser() -> None:
    page = FakePage(
        {
            "status": "unsupported",
            "page_url": "https://example.test/unsupported",
            "page_origin": "https://example.test",
            "permission_state": "unsupported",
            "tools": [],
            "diagnostics": [
                {
                    "code": "webmcp_unavailable",
                    "path": "document.modelContext",
                    "message": "The page does not expose the WebMCP discovery API.",
                }
            ],
        },
        url="https://example.test/unsupported",
    )

    snapshot = asyncio.run(discover_page_tools(page))

    assert snapshot.status is NativeDiscoveryStatus.UNSUPPORTED
    assert snapshot.permissions_policy.state is PermissionPolicyState.UNSUPPORTED
    assert snapshot.diagnostics[0].code == "webmcp_unavailable"


def test_discovery_reports_permission_blocking() -> None:
    page = FakePage(
        {
            "status": "blocked",
            "page_url": "https://example.test/blocked",
            "page_origin": "https://example.test",
            "permission_state": "denied",
            "tools": [],
            "diagnostics": [
                {
                    "code": "permission_denied",
                    "path": "document.modelContext.getTools",
                    "message": "The WebMCP tools feature is blocked by Permissions Policy.",
                }
            ],
        },
        url="https://example.test/blocked",
    )

    snapshot = asyncio.run(discover_page_tools(page))

    assert snapshot.status is NativeDiscoveryStatus.BLOCKED
    assert snapshot.permissions_policy.state is PermissionPolicyState.DENIED
    assert snapshot.diagnostics[0].code == "permission_denied"


def test_discovery_keeps_malformed_metadata_diagnosable() -> None:
    page = FakePage(
        {
            "status": "supported",
            "page_url": "https://example.test/malformed",
            "page_origin": "https://example.test",
            "permission_state": "allowed",
            "tools": [
                {
                    "name": "bad name",
                    "description": "",
                    "input_schema": "not json",
                "origin": 42,
                "annotations": {"readOnlyHint": "yes"},
                },
                "not an object",
            ],
            "diagnostics": [],
        },
        url="https://example.test/malformed",
    )

    snapshot = asyncio.run(discover_page_tools(page))

    assert snapshot.status is NativeDiscoveryStatus.SUPPORTED
    assert len(snapshot.tools) == 2
    assert all(not tool.valid for tool in snapshot.tools)
    codes = {diagnostic.code for diagnostic in snapshot.diagnostics}
    assert codes == {
        "invalid_name",
        "malformed_description",
        "malformed_origin",
        "malformed_input_schema",
        "malformed_annotation_value",
        "malformed_tool",
    }


def test_discovery_reports_duplicate_tool_names() -> None:
    payload = _supported_payload()
    payload["tools"] = [
        {
            "name": "same",
            "description": "First.",
            "input_schema": None,
            "origin": "https://example.test",
        },
        {
            "name": "same",
            "description": "Second.",
            "input_schema": None,
            "origin": "https://example.test",
        },
    ]

    snapshot = asyncio.run(discover_page_tools(FakePage(payload)))

    assert [tool.valid for tool in snapshot.tools] == [True, False]
    assert [diagnostic.code for diagnostic in snapshot.diagnostics] == [
        "duplicate_tool_name"
    ]


def test_discovery_evaluation_failure_is_structured() -> None:
    class FailingPage(FakePage):
        async def evaluate(self, expression: str, arg: object = ...) -> object:
            raise RuntimeError("evaluation failed")

    snapshot = asyncio.run(discover_page_tools(FailingPage({}, url="https://example.test/fail")))

    assert snapshot.status is NativeDiscoveryStatus.ERROR
    assert snapshot.diagnostics[0].code == "evaluation_failed"
    assert snapshot.page_origin == "https://example.test"


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    async def new_context(self) -> FakeContext:
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.chromium = self
        self.launch_options: dict[str, object] = {}

    async def launch(self, **options: object) -> FakeBrowser:
        self.launch_options = options
        assert options["headless"] is True
        return self.browser


class FakePlaywrightContext:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


def test_discover_url_writes_content_addressed_snapshot(tmp_path: Path) -> None:
    page = FakePage(_supported_payload())
    context = FakeContext(page)
    browser = FakeBrowser(context)

    async def goto(url: str, *, wait_until: str, timeout: int) -> None:
        assert (url, wait_until, timeout) == (
            "https://example.test/page",
            "domcontentloaded",
            30000,
        )

    page.goto = goto  # type: ignore[attr-defined]
    playwright = FakePlaywright(browser)

    snapshot, trace_path = asyncio.run(
        discover_url(
            "https://example.test/page",
            trace_dir=tmp_path,
            browser_channel="chrome-canary",
            browser_args=["--enable-features=WebMCPTesting"],
            playwright_factory=lambda: FakePlaywrightContext(playwright),
        )
    )

    assert snapshot.status is NativeDiscoveryStatus.SUPPORTED
    assert trace_path is not None
    saved = Path(trace_path)
    assert saved.name == f"webmcp-{snapshot.snapshot_digest.removeprefix('sha256:')}.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["snapshot_digest"] == (
        snapshot.snapshot_digest
    )
    assert context.closed is True
    assert browser.closed is True
    assert playwright.launch_options == {
        "headless": True,
        "channel": "chrome-canary",
        "args": ["--enable-features=WebMCPTesting"],
    }


def test_write_discovery_snapshot_is_repeatable(tmp_path: Path) -> None:
    page = FakePage(_supported_payload())
    snapshot = asyncio.run(discover_page_tools(page))

    first = write_discovery_snapshot(snapshot, tmp_path)
    second = write_discovery_snapshot(snapshot, tmp_path)

    assert first == second
    assert Path(first).read_text(encoding="utf-8") == Path(second).read_text(encoding="utf-8")


def test_discover_command_reports_snapshot_and_trace(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict[str, object] = {}

    async def fake_discover(*args, **kwargs):
        captured.update(kwargs)
        snapshot = await discover_page_tools(FakePage(_supported_payload()))
        return snapshot, str(tmp_path / "snapshot.json")

    monkeypatch.setattr("browser_to_mcp.cli.discover_url", fake_discover)

    assert (
        run(
            [
                "discover",
                "https://example.test/page",
                "--trace-dir",
                str(tmp_path),
                "--browser-channel",
                "chrome-canary",
                "--browser-arg=--enable-features=WebMCPTesting",
            ]
        )
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "supported"
    assert result["trace_path"] == str(tmp_path / "snapshot.json")
    assert captured["browser_channel"] == "chrome-canary"
    assert captured["browser_args"] == ["--enable-features=WebMCPTesting"]
