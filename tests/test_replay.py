from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import browser_to_mcp.replay as replay_module
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.models import StepAction, WorkflowInput
from browser_to_mcp.replay import (
    ExecutionFailureCategory,
    WorkflowExecutionError,
    evaluate_success_assertions,
    execute_steps,
    parse_input_pairs,
    render_template,
)

WORKFLOW_PATH = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


class FakeLocator:
    """Minimal locator double for deterministic replay tests."""

    async def inner_text(self, timeout: int) -> str:
        assert timeout == 30000
        return "Matching Hacker News results"


class FakePage:
    """Minimal browser page double for supported V0 steps."""

    def __init__(self) -> None:
        self.goto_calls: list[dict[str, object]] = []
        self.selectors: list[str] = []

    async def goto(self, url: str, **kwargs: object) -> None:
        self.goto_calls.append({"url": url, **kwargs})

    def locator(self, selector: str) -> FakeLocator:
        self.selectors.append(selector)
        return FakeLocator()


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
        return FailingLocator()


class FakeContext:
    """A browser context double that records cleanup."""

    def __init__(self, page: FakePage) -> None:
        self.closed = False
        self.page = page

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    """A browser double that records cleanup."""

    def __init__(self, context: FakeContext) -> None:
        self.closed = False
        self.context = context

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
def test_parse_input_pairs_rejects_invalid_declared_types(
    input_type: str, raw_value: str
) -> None:
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

    outputs = asyncio.run(execute_steps(page, workflow, {"query": "mcp"}))

    assert page.goto_calls == [
        {
            "url": "https://hn.algolia.com/?q=mcp",
            "wait_until": "domcontentloaded",
            "timeout": 30000,
        }
    ]
    assert page.selectors == ["main"]
    assert outputs == {"results": "Matching Hacker News results"}


def test_execute_steps_rejects_an_action_outside_the_current_replay_scope() -> None:
    workflow = load_workflow(WORKFLOW_PATH).workflow
    unsupported_workflow = workflow.model_copy(
        update={
            "steps": [
                workflow.steps[0],
                workflow.steps[1].model_copy(update={"action": StepAction.CLICK}),
            ]
        }
    )

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(execute_steps(FakePage(), unsupported_workflow, {"query": "mcp"}))

    assert error.value.category is ExecutionFailureCategory.RUNTIME
    assert error.value.path == "steps.results"


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
