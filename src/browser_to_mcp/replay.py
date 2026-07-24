"""Deterministic replay of the V0 read-only reference workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_to_mcp.loader import LoadedWorkflow
from browser_to_mcp.models import StepAction, SuccessAssertion, Workflow, WorkflowInput

_TEMPLATE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


class ExecutionFailureCategory(StrEnum):
    """Stable failures surfaced by replay without changing workflow validation errors."""

    ASSERTION = "assertion"
    INPUT = "input"
    RUNTIME = "runtime"


@dataclass(slots=True)
class WorkflowExecutionError(Exception):
    """An execution failure that can be returned by the CLI or future MCP server."""

    category: ExecutionFailureCategory
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.category}: {self.path}: {self.message}"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """The structured result of one successful workflow replay."""

    workflow: str
    manifest_digest: str
    outputs: dict[str, str]
    duration_seconds: float


def parse_input_pairs(input_pairs: list[str], workflow: Workflow) -> dict[str, object]:
    """Parse and type-check CLI input pairs before browser startup."""

    raw_inputs: dict[str, str] = {}
    for pair in input_pairs:
        if "=" not in pair:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.INPUT,
                "inputs",
                "Inputs must use NAME=VALUE format.",
            )
        name, value = pair.split("=", maxsplit=1)
        if not name:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.INPUT,
                "inputs",
                "Input names cannot be empty.",
            )
        if name in raw_inputs:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.INPUT,
                f"inputs.{name}",
                "Each input may be provided only once.",
            )
        raw_inputs[name] = value

    unknown_inputs = sorted(set(raw_inputs).difference(workflow.inputs))
    if unknown_inputs:
        name = unknown_inputs[0]
        raise WorkflowExecutionError(
            ExecutionFailureCategory.INPUT,
            f"inputs.{name}",
            "Input is not declared by the workflow.",
        )

    parsed_inputs: dict[str, object] = {}
    for name, definition in workflow.inputs.items():
        raw_value = raw_inputs.get(name)
        if raw_value is None:
            if definition.required:
                raise WorkflowExecutionError(
                    ExecutionFailureCategory.INPUT,
                    f"inputs.{name}",
                    "A required input was not provided.",
                )
            continue
        parsed_inputs[name] = _parse_input_value(name, raw_value, definition)
    return parsed_inputs


def _parse_input_value(name: str, value: str, definition: WorkflowInput) -> object:
    if definition.type == "string":
        if definition.min_length is not None and len(value) < definition.min_length:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.INPUT,
                f"inputs.{name}",
                f"Input must contain at least {definition.min_length} characters.",
            )
        return value

    try:
        if definition.type == "integer":
            return int(value)
        if definition.type == "number":
            return float(value)
        if definition.type == "boolean" and value.lower() in {"true", "false"}:
            return value.lower() == "true"
    except ValueError as error:
        raise WorkflowExecutionError(
            ExecutionFailureCategory.INPUT,
            f"inputs.{name}",
            f"Input must be a {definition.type}.",
        ) from error

    raise WorkflowExecutionError(
        ExecutionFailureCategory.INPUT,
        f"inputs.{name}",
        f"Input must be a {definition.type}.",
    )


async def replay_workflow(loaded: LoadedWorkflow, input_pairs: list[str]) -> ReplayResult:
    """Replay the supported V0 steps in a fresh headless Chromium context."""

    inputs = parse_input_pairs(input_pairs, loaded.workflow)
    started_at = monotonic()
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    outputs = await execute_steps(page, loaded.workflow, inputs)
                    evaluate_success_assertions(loaded.workflow.success, outputs)
                finally:
                    await context.close()
            finally:
                await browser.close()
    except WorkflowExecutionError:
        raise
    except PlaywrightTimeoutError as error:
        raise WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME,
            "replay",
            "Browser operation timed out.",
        ) from error
    except Exception as error:
        raise WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME,
            "replay",
            _runtime_message(error),
        ) from error

    return ReplayResult(
        workflow=loaded.workflow.name,
        manifest_digest=loaded.manifest_digest,
        outputs=outputs,
        duration_seconds=monotonic() - started_at,
    )


async def execute_steps(
    page: Page,
    workflow: Workflow,
    inputs: dict[str, object],
) -> dict[str, str]:
    """Execute the reference workflow's declared navigation and extraction steps."""

    outputs: dict[str, str] = {}
    for step in workflow.steps:
        if step.action is StepAction.GOTO:
            await page.goto(
                render_template(step.url or "", inputs),
                wait_until="domcontentloaded",
                timeout=step.timeout_seconds * 1000,
            )
        elif step.action is StepAction.EXTRACT_TEXT:
            extracted = await page.locator(render_template(step.selector or "", inputs)).inner_text(
                timeout=step.timeout_seconds * 1000
            )
            if step.id in workflow.outputs:
                outputs[step.id] = extracted
        else:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.RUNTIME,
                f"steps.{step.id}",
                f"Replay does not yet support action '{step.action}'.",
            )
    return outputs


def render_template(template: str, inputs: dict[str, object]) -> str:
    """Render declared workflow input placeholders without evaluating arbitrary expressions."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in inputs:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.INPUT,
                f"inputs.{name}",
                "Template references an input that was not provided.",
            )
        value = inputs[name]
        return str(value).lower() if isinstance(value, bool) else str(value)

    return _TEMPLATE_PATTERN.sub(replace, template)


def evaluate_success_assertions(
    assertions: list[SuccessAssertion], outputs: dict[str, str]
) -> None:
    """Verify V0 post-run output assertions before reporting replay success."""

    for assertion in assertions:
        value = outputs.get(assertion.field)
        if assertion.assertion == "output_not_empty" and (value is None or not value.strip()):
            raise WorkflowExecutionError(
                ExecutionFailureCategory.ASSERTION,
                f"success.{assertion.id}",
                f"Output '{assertion.field}' is empty or was not extracted.",
            )


def _runtime_message(error: Exception) -> str:
    message = str(error)
    if "Executable doesn't exist" in message:
        return "Chromium is not installed. Run 'uv run playwright install chromium'."
    return message or error.__class__.__name__
