from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import browser_to_mcp.replay as replay_module
from browser_to_mcp.loader import LoadedWorkflow, load_workflow, manifest_digest
from browser_to_mcp.models import (
    Confirmation,
    Idempotency,
    Retry,
    Risk,
    StepAction,
    WorkflowInput,
    WorkflowOutput,
    WorkflowStep,
)
from browser_to_mcp.replay import (
    ExecutionFailureCategory,
    PlaywrightReplayEngine,
    WorkflowExecutionError,
    _is_allowed_url,
    evaluate_success_assertions,
    execute_steps,
    parse_input_pairs,
    render_template,
)
from browser_to_mcp.validation import FailureCategory, WorkflowValidationError

WORKFLOW_PATH = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


def read_only_step(**fields: object) -> WorkflowStep:
    """Build explicit V0 step declarations without hiding their policy values."""

    defaults: dict[str, object] = {
        "timeout_seconds": 30,
        "risk": Risk.READ_ONLY,
        "idempotency": Idempotency.SAFE,
        "retry": Retry.NONE,
        "confirmation": Confirmation.NEVER,
    }
    defaults.update(fields)
    return WorkflowStep(**defaults)


class FakeLocator:
    """Minimal locator double for deterministic replay tests."""

    def __init__(self, page: FakePage | None = None, value: str = "") -> None:
        self.page = page
        self.value = value

    async def inner_text(self, timeout: int) -> str:
        assert timeout == 30000
        return "Matching Hacker News results"

    async def fill(self, value: str, timeout: int) -> None:
        assert self.page is not None
        self.page.fill_calls.append({"selector": self.value, "value": value, "timeout": timeout})

    async def click(self, timeout: int) -> None:
        assert self.page is not None
        self.page.click_calls.append({"selector": self.value, "timeout": timeout})

    async def wait_for(self, state: str, timeout: int) -> None:
        assert self.page is not None
        self.page.wait_calls.append({"text": self.value, "state": state, "timeout": timeout})


class FakeKeyboard:
    """Keyboard double for declared press steps."""

    def __init__(self, page: FakePage) -> None:
        self.page = page

    async def press(self, key: str) -> None:
        self.page.press_calls.append(key)


class SlowKeyboard(FakeKeyboard):
    """Keyboard double that exceeds a one-second step timeout."""

    async def press(self, key: str) -> None:
        await asyncio.sleep(2)


class FakeDownload:
    """Download double exposing the metadata returned by Playwright."""

    suggested_filename = "result.txt"


class FakeDownloadInfo:
    """Async download expectation double."""

    def __init__(self) -> None:
        self.download = FakeDownload()

    async def __aenter__(self) -> FakeDownloadInfo:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    @property
    def value(self) -> Coroutine[Any, Any, FakeDownload]:
        async def get_download() -> FakeDownload:
            return self.download

        return get_download()


class FakePage:
    """Minimal browser page double for supported V0 steps."""

    def __init__(self) -> None:
        self.goto_calls: list[dict[str, object]] = []
        self.selectors: list[str] = []
        self.fill_calls: list[dict[str, object]] = []
        self.click_calls: list[dict[str, object]] = []
        self.press_calls: list[str] = []
        self.wait_calls: list[dict[str, object]] = []
        self.keyboard = FakeKeyboard(self)

    async def goto(self, url: str, **kwargs: object) -> None:
        self.goto_calls.append({"url": url, **kwargs})

    def locator(self, selector: str) -> FakeLocator:
        self.selectors.append(selector)
        return FakeLocator(self, selector)

    def get_by_text(self, text: str) -> FakeLocator:
        return FakeLocator(self, text)

    def expect_download(self, timeout: int) -> FakeDownloadInfo:
        assert timeout == 30000
        return FakeDownloadInfo()


class SlowKeyboardPage(FakePage):
    """Page double for bounded keyboard execution."""

    def __init__(self) -> None:
        super().__init__()
        self.keyboard = SlowKeyboard(self)


class FailingPage(FakePage):
    """A page double that raises a browser error on navigation."""

    async def goto(self, url: str, **kwargs: object) -> None:
        raise PlaywrightError("net::ERR_NAME_NOT_RESOLVED")


class FailingLocator(FakeLocator):
    """A locator double that raises the browser's selector failure."""

    async def inner_text(self, timeout: int) -> str:
        raise PlaywrightError("Locator not found: main")


class SelectorFailurePage(FakePage):
    """A page double that fails while extracting a declared selector."""

    def locator(self, selector: str) -> FailingLocator:
        self.selectors.append(selector)
        return FailingLocator(self, selector)


class ScreenshotPage(FailingPage):
    """Failing page double that can persist a failure screenshot."""

    async def screenshot(self, path: str, full_page: bool) -> None:
        assert full_page is True
        Path(path).write_bytes(b"png-test-data")


class SensitiveErrorPage(ScreenshotPage):
    """Failing page double whose diagnostic contains the sensitive input."""

    async def goto(self, url: str, **kwargs: object) -> None:
        raise PlaywrightError("navigation failed while processing secret")


class SensitiveOutputLocator(FakeLocator):
    """Locator double returning output that contains the sensitive input."""

    async def inner_text(self, timeout: int) -> str:
        assert timeout == 30000
        return "visible secret"


class SensitiveOutputPage(FakePage):
    """Page double for output redaction coverage."""

    def locator(self, selector: str) -> SensitiveOutputLocator:
        self.selectors.append(selector)
        return SensitiveOutputLocator(self, selector)


class FakeContext:
    """A browser context double that records cleanup."""

    def __init__(self, page: FakePage) -> None:
        self.closed = False
        self.page = page
        self.route_patterns: list[str] = []

    async def route(self, pattern: str, handler: object) -> None:
        self.route_patterns.append(pattern)

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    """A browser double that records cleanup."""

    def __init__(self, context: FakeContext) -> None:
        self.closed = False
        self.context = context
        self.version = "149.0.0"

    async def new_context(self) -> FakeContext:
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    """A Chromium launcher double for replay runtime failure tests."""

    def __init__(self, browser: FakeBrowser | None = None, error: Exception | None = None) -> None:
        self.browser = browser
        self.error = error

    async def launch(self, headless: bool) -> FakeBrowser:
        assert headless is True
        if self.error is not None:
            raise self.error
        assert self.browser is not None
        return self.browser


class FreshChromium:
    """Launcher double that creates a new browser context for every run."""

    def __init__(self) -> None:
        self.browsers: list[FakeBrowser] = []

    async def launch(self, headless: bool) -> FakeBrowser:
        assert headless is True
        browser = FakeBrowser(FakeContext(FakePage()))
        self.browsers.append(browser)
        return browser


class SlowContext(FakeContext):
    """Context double that can be interrupted by the engine timeout."""

    async def new_page(self) -> FakePage:
        await asyncio.sleep(2)
        return await super().new_page()


class FakePlaywright:
    """The portion of Playwright used by the replay runtime."""

    def __init__(self, chromium: FakeChromium) -> None:
        self.chromium = chromium


class FakePlaywrightManager:
    """Async context manager returned by the patched Playwright factory."""

    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def __aenter__(self) -> FakePlaywright:
        return self.playwright

    async def __aexit__(self, *args: object) -> None:
        return None


def patch_playwright(monkeypatch: pytest.MonkeyPatch, chromium: FakeChromium) -> None:
    """Replace the real Playwright factory with a controlled runtime double."""

    monkeypatch.setattr(
        replay_module,
        "async_playwright",
        lambda: FakePlaywrightManager(FakePlaywright(chromium)),
    )


def test_parse_input_pairs_rejects_unknown_input() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow

    with pytest.raises(WorkflowExecutionError) as error:
        parse_input_pairs(["query=mcp", "extra=value"], workflow)

    assert error.value.category is ExecutionFailureCategory.INPUT
    assert error.value.path == "inputs.extra"


def test_parse_input_pairs_rejects_short_string() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow

    with pytest.raises(WorkflowExecutionError) as error:
        parse_input_pairs(["query="], workflow)

    assert error.value.category is ExecutionFailureCategory.INPUT
    assert error.value.path == "inputs.query"


@pytest.mark.parametrize(
    ("input_pairs", "path", "message"),
    [
        (["query"], "inputs", "Inputs must use NAME=VALUE format."),
        (["=mcp"], "inputs", "Input names cannot be empty."),
    ],
)
def test_parse_input_pairs_rejects_malformed_pairs(
    input_pairs: list[str], path: str, message: str
) -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow

    with pytest.raises(WorkflowExecutionError) as error:
        parse_input_pairs(input_pairs, workflow)

    assert error.value.category is ExecutionFailureCategory.INPUT
    assert error.value.path == path
    assert error.value.message == message


def test_parse_input_pairs_rejects_duplicate_input() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow

    with pytest.raises(WorkflowExecutionError) as error:
        parse_input_pairs(["query=first", "query=second"], workflow)

    assert error.value.category is ExecutionFailureCategory.INPUT
    assert error.value.path == "inputs.query"


@pytest.mark.parametrize(
    ("input_type", "raw_value", "expected"),
    [
        ("integer", "42", 42),
        ("number", "1.25", 1.25),
        ("boolean", "true", True),
    ],
)
def test_parse_input_pairs_coerces_declared_types(
    input_type: str, raw_value: str, expected: object
) -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow.model_copy(
        update={"inputs": {"value": WorkflowInput(type=input_type)}}
    )

    assert parse_input_pairs([f"value={raw_value}"], workflow) == {"value": expected}


@pytest.mark.parametrize(
    ("input_type", "raw_value"),
    [
        ("integer", "1.25"),
        ("number", "not-a-number"),
        ("boolean", "yes"),
    ],
)
def test_parse_input_pairs_rejects_invalid_declared_types(input_type: str, raw_value: str) -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow.model_copy(
        update={"inputs": {"value": WorkflowInput(type=input_type)}}
    )

    with pytest.raises(WorkflowExecutionError) as error:
        parse_input_pairs([f"value={raw_value}"], workflow)

    assert error.value.category is ExecutionFailureCategory.INPUT
    assert error.value.path == "inputs.value"


def test_render_template_rejects_missing_value() -> None:
    with pytest.raises(WorkflowExecutionError) as error:
        render_template("https://example.com/?q={{query}}", {})

    assert error.value.category is ExecutionFailureCategory.INPUT
    assert error.value.path == "inputs.query"


def test_execute_steps_replays_reference_workflow() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow
    page = FakePage()

    execution = asyncio.run(execute_steps(page, workflow, {"query": "mcp"}))

    assert page.goto_calls == [
        {
            "url": "https://hn.algolia.com/?q=mcp",
            "wait_until": "domcontentloaded",
            "timeout": 30000,
        }
    ]
    assert page.selectors == ["main"]
    assert execution.outputs == {"results": "Matching Hacker News results"}
    assert execution.actions[0].step_id == "search"
    assert execution.actions[0].action is StepAction.GOTO
    assert execution.actions[1].step_id == "results"
    assert execution.actions[1].output_key == "results"


@pytest.mark.parametrize(
    ("url", "allowed", "expected"),
    [
        ("https://hn.algolia.com/", ["hn.algolia.com"], True),
        ("https://api.hn.algolia.com/results", ["hn.algolia.com"], True),
        ("https://not-hn.algolia.com/", ["hn.algolia.com"], False),
        ("https://hn.algolia.com.evil.example/", ["hn.algolia.com"], False),
        ("about:blank", ["hn.algolia.com"], True),
        ("data:text/plain,results", ["hn.algolia.com"], True),
        ("file:///tmp/results", ["hn.algolia.com"], False),
    ],
)
def test_request_policy_matches_declared_hosts_only(
    url: str, allowed: list[str], expected: bool
) -> None:
    assert _is_allowed_url(url, allowed) is expected


def test_execute_steps_rejects_runtime_navigation_outside_allowlist() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow.model_copy(
        update={
            "steps": [
                read_only_step(
                    id="search",
                    action=StepAction.GOTO,
                    url="https://evil.example/results",
                )
            ]
        }
    )
    page = FakePage()

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(
            execute_steps(
                page,
                workflow,
                {},
                allowed_domains=workflow.permissions.allowed_domains,
            )
        )

    assert error.value.category is ExecutionFailureCategory.PERMISSION
    assert error.value.path == "steps.search.url"
    assert page.goto_calls == []


def test_execute_steps_reports_blocked_page_request() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(
            execute_steps(
                FakePage(),
                workflow,
                {"query": "mcp"},
                allowed_domains=workflow.permissions.allowed_domains,
                blocked_request_getter=lambda: "https://evil.example/script.js",
            )
        )

    assert error.value.category is ExecutionFailureCategory.PERMISSION
    assert error.value.path == "steps.search"
    assert "evil.example" in error.value.message


def test_execute_steps_runs_declared_read_only_actions_in_order() -> None:
    base_workflow = load_workflow(WORKFLOW_PATH).workflow
    workflow = base_workflow.model_copy(
        update={
            "steps": [
                read_only_step(
                    id="navigate",
                    action=StepAction.GOTO,
                    url="https://hn.algolia.com/",
                ),
                read_only_step(
                    id="fill_query",
                    action=StepAction.FILL,
                    selector="#search",
                    value="{{query}}",
                ),
                read_only_step(id="submit", action=StepAction.PRESS, key="Enter"),
                read_only_step(id="click_more", action=StepAction.CLICK, selector="#more"),
                read_only_step(
                    id="wait_results",
                    action=StepAction.WAIT_FOR_TEXT,
                    text="Results",
                ),
                read_only_step(id="results", action=StepAction.EXTRACT_TEXT, selector="main"),
                read_only_step(id="save", action=StepAction.DOWNLOAD, selector="#download"),
            ],
            "outputs": {
                **base_workflow.outputs,
                "save": WorkflowOutput(type="string"),
            },
        }
    )
    page = FakePage()

    execution = asyncio.run(execute_steps(page, workflow, {"query": "mcp"}))

    assert page.fill_calls == [{"selector": "#search", "value": "mcp", "timeout": 30000}]
    assert page.press_calls == ["Enter"]
    assert page.click_calls == [
        {"selector": "#more", "timeout": 30000},
        {"selector": "#download", "timeout": 30000},
    ]
    assert page.wait_calls == [{"text": "Results", "state": "visible", "timeout": 30000}]
    assert execution.outputs == {
        "results": "Matching Hacker News results",
        "save": "result.txt",
    }
    assert [action.step_id for action in execution.actions] == [
        "navigate",
        "fill_query",
        "submit",
        "click_more",
        "wait_results",
        "results",
        "save",
    ]


def test_execute_steps_rejects_an_action_outside_the_current_replay_scope() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow
    unsupported_step = workflow.steps[1].model_construct(
        id="unsupported",
        action="future_action",
        selector="main",
        timeout_seconds=30,
    )
    unsupported_workflow = workflow.model_copy(
        update={
            "steps": [
                workflow.steps[0],
                unsupported_step,
            ]
        }
    )

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(execute_steps(FakePage(), unsupported_workflow, {"query": "mcp"}))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.path == "steps.unsupported"


def test_execute_steps_bounds_keyboard_presses() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow.model_copy(
        update={
            "steps": [
                read_only_step(
                    id="submit",
                    action=StepAction.PRESS,
                    key="Enter",
                    timeout_seconds=1,
                )
            ]
        }
    )

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(execute_steps(SlowKeyboardPage(), workflow, {"query": "mcp"}))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.path == "steps.submit"
    assert error.value.message == "Action 'press' timed out after 1 seconds."


def test_success_assertion_rejects_whitespace_output() -> None:
    assertions = load_workflow(WORKFLOW_PATH).workflow.success

    with pytest.raises(WorkflowExecutionError) as error:
        evaluate_success_assertions(assertions, {"results": "  "})

    assert error.value.category is ExecutionFailureCategory.ASSERTION
    assert error.value.path == "success.results_present"


def test_success_assertion_rejects_missing_output() -> None:
    assertions = load_workflow(WORKFLOW_PATH).workflow.success

    with pytest.raises(WorkflowExecutionError) as error:
        evaluate_success_assertions(assertions, {})

    assert error.value.category is ExecutionFailureCategory.ASSERTION
    assert error.value.path == "success.results_present"


def test_engine_uses_a_fresh_context_for_each_run(monkeypatch: pytest.MonkeyPatch) -> None:
    chromium = FreshChromium()
    patch_playwright(monkeypatch, chromium)
    workflow = load_workflow(WORKFLOW_PATH).workflow
    engine = PlaywrightReplayEngine()

    first = asyncio.run(engine.run(workflow, {"query": "first"}))
    second = asyncio.run(engine.run(workflow, {"query": "second"}))

    assert first.execution.outputs == {"results": "Matching Hacker News results"}
    assert second.execution.outputs == {"results": "Matching Hacker News results"}
    assert len(chromium.browsers) == 2
    assert chromium.browsers[0].context is not chromium.browsers[1].context
    assert all(browser.context.closed for browser in chromium.browsers)
    assert all(browser.closed for browser in chromium.browsers)


def test_engine_maps_overall_timeout_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SlowContext(FakePage())
    browser = FakeBrowser(context)
    patch_playwright(monkeypatch, FakeChromium(browser=browser))
    workflow = load_workflow(WORKFLOW_PATH).workflow

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(PlaywrightReplayEngine(overall_timeout_seconds=1).run(workflow, {}))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.path == "replay"
    assert error.value.message == "Workflow exceeded overall timeout of 1 seconds."
    assert context.closed is True
    assert browser.closed is True


def test_engine_preserves_cancellation_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SlowContext(FakePage())
    browser = FakeBrowser(context)
    patch_playwright(monkeypatch, FakeChromium(browser=browser))
    workflow = load_workflow(WORKFLOW_PATH).workflow

    async def cancel_run() -> None:
        task = asyncio.create_task(PlaywrightReplayEngine().run(workflow, {}))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_run())

    assert context.closed is True
    assert browser.closed is True


def test_replay_boundary_revalidates_unsafe_workflow() -> None:
    source = load_workflow(WORKFLOW_PATH)
    unsafe_workflow = source.workflow.model_copy(update={"risk": Risk.FINANCIAL})
    unsafe = LoadedWorkflow(
        workflow=unsafe_workflow,
        manifest_digest=manifest_digest(unsafe_workflow),
    )

    with pytest.raises(WorkflowValidationError) as error:
        asyncio.run(replay_module.replay_workflow(unsafe, ["query=mcp"]))

    assert error.value.category is FailureCategory.POLICY
    assert error.value.path == "risk"


def test_replay_reports_missing_chromium(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_playwright(monkeypatch, FakeChromium(error=PlaywrightError("Executable doesn't exist")))
    loaded = load_workflow(WORKFLOW_PATH)

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(replay_module.replay_workflow(loaded, ["query=mcp"]))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.message == (
        "Chromium is not installed. Run 'uv run playwright install chromium'."
    )


def test_replay_maps_playwright_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_playwright(monkeypatch, FakeChromium(error=PlaywrightTimeoutError("Timed out")))
    loaded = load_workflow(WORKFLOW_PATH)

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(replay_module.replay_workflow(loaded, ["query=mcp"]))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.message == "Browser operation timed out."


def test_replay_maps_browser_navigation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    context = FakeContext(FailingPage())
    patch_playwright(monkeypatch, FakeChromium(browser=FakeBrowser(context)))
    loaded = load_workflow(WORKFLOW_PATH)

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(replay_module.replay_workflow(loaded, ["query=mcp"]))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.message == "net::ERR_NAME_NOT_RESOLVED"
    assert context.closed is True
    assert error.value.actions[-1].status is replay_module.ActionStatus.FAILED


def test_replay_maps_selector_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    context = FakeContext(SelectorFailurePage())
    patch_playwright(monkeypatch, FakeChromium(browser=FakeBrowser(context)))
    loaded = load_workflow(WORKFLOW_PATH)

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(replay_module.replay_workflow(loaded, ["query=mcp"]))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.message == "Locator not found: main"
    assert context.closed is True


def test_replay_closes_resources_after_step_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    context = FakeContext(FakePage())
    browser = FakeBrowser(context)
    patch_playwright(monkeypatch, FakeChromium(browser=browser))

    async def fail_steps(*args: object, **kwargs: object) -> dict[str, str]:
        raise WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME,
            "steps.search",
            "Controlled step failure.",
        )

    monkeypatch.setattr(replay_module, "execute_steps", fail_steps)

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(replay_module.replay_workflow(load_workflow(WORKFLOW_PATH), ["query=mcp"]))

    assert error.value.path == "steps.search"
    assert context.closed is True
    assert browser.closed is True


def test_replay_writes_redacted_success_trace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = FakeContext(SensitiveOutputPage())
    patch_playwright(monkeypatch, FakeChromium(browser=FakeBrowser(context)))
    source = load_workflow(WORKFLOW_PATH)
    workflow = source.workflow.model_copy(
        update={"inputs": {"query": WorkflowInput(type="string", sensitive=True, min_length=1)}}
    )
    loaded = LoadedWorkflow(workflow=workflow, manifest_digest=manifest_digest(workflow))

    result = asyncio.run(replay_module.replay_workflow(loaded, ["query=secret"], tmp_path))

    assert result.trace_path is not None
    trace = json.loads(Path(result.trace_path).read_text(encoding="utf-8"))
    assert trace["status"] == "completed"
    assert trace["inputs"] == {"query": "[REDACTED]"}
    assert trace["outputs"]["results"] == "visible [REDACTED]"
    assert trace["assertions"] == [
        {
            "assertion": "output_not_empty",
            "assertion_id": "results_present",
            "field": "results",
            "status": "passed",
        }
    ]
    assert trace["actions"][0]["duration_seconds"] >= 0
    assert trace["metadata"]["manifest_digest"] == loaded.manifest_digest
    assert trace["metadata"]["playwright_version"] != ""


def test_replay_writes_failure_trace_and_screenshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = FakeContext(SensitiveErrorPage())
    patch_playwright(monkeypatch, FakeChromium(browser=FakeBrowser(context)))
    source = load_workflow(WORKFLOW_PATH)
    workflow = source.workflow.model_copy(
        update={"inputs": {"query": WorkflowInput(type="string", sensitive=True, min_length=1)}}
    )
    loaded = LoadedWorkflow(workflow=workflow, manifest_digest=manifest_digest(workflow))

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(replay_module.replay_workflow(loaded, ["query=secret"], tmp_path))

    assert error.value.trace_path is not None
    trace = json.loads(Path(error.value.trace_path).read_text(encoding="utf-8"))
    assert trace["status"] == "failed"
    assert trace["inputs"] == {"query": "[REDACTED]"}
    assert trace["error"]["category"] == "runtime"
    assert trace["error"]["path"] == "steps.search"
    assert trace["error"]["screenshot_path"] in trace["screenshots"]
    assert Path(trace["error"]["screenshot_path"]).read_bytes() == b"png-test-data"
    assert trace["actions"][-1]["status"] == "failed"
    assert "secret" not in Path(error.value.trace_path).read_text(encoding="utf-8")
