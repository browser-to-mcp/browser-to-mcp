from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from browser_to_mcp.loader import load_workflow
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
