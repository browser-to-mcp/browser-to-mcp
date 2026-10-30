"""Deterministic replay of the V0 read-only reference workflow."""

from __future__ import annotations

import asyncio
import platform
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path
from time import monotonic
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_to_mcp.browser_policy import (
    BrowserBoundary,
    BrowserViolation,
    request_violation,
)
from browser_to_mcp.loader import LoadedWorkflow
from browser_to_mcp.models import (
    OutputValue,
    StepAction,
    StructuredOutput,
    SuccessAssertion,
    Workflow,
    WorkflowInput,
)
from browser_to_mcp.paging import PagingPermissionError, collect_pages
from browser_to_mcp.records import extract_records
from browser_to_mcp.structured_outputs import OutputValidationError, validate_outputs
from browser_to_mcp.tracing import (
    TraceAction,
    TraceAssertion,
    TraceError,
    TraceMetadata,
    TracePage,
    TraceStatus,
    WorkflowTrace,
    has_sensitive_outputs,
    redact_inputs,
    redact_outputs,
    redact_text,
    write_trace,
)
from browser_to_mcp.url_templates import TEMPLATE_PATTERN, validate_url_template
from browser_to_mcp.validation import validate_workflow_policy


class ExecutionFailureCategory(StrEnum):
    """Stable failures surfaced by replay without changing workflow validation errors."""

    ASSERTION = "assertion"
    INPUT = "input"
    PERMISSION = "permission"
    RUNTIME = "runtime"


class ActionStatus(StrEnum):
    """Result state for a declared browser action."""

    COMPLETED = "completed"
    FAILED = "failed"


class AssertionStatus(StrEnum):
    """Result state for a post-run workflow assertion."""

    PASSED = "passed"
    FAILED = "failed"


@dataclass(slots=True)
class WorkflowExecutionError(Exception):
    """An execution failure that can be returned by the CLI or future MCP server."""

    category: ExecutionFailureCategory
    path: str
    message: str
    actions: tuple[ActionResult, ...] = ()
    assertions: tuple[AssertionResult, ...] = ()
    browser_version: str = "unknown"
    screenshot_path: str | None = None
    trace_path: str | None = None

    def __str__(self) -> str:
        return f"{self.category}: {self.path}: {self.message}"


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """The structured result of one successful workflow replay."""

    workflow: str
    manifest_digest: str
    outputs: dict[str, OutputValue]
    actions: tuple[ActionResult, ...]
    duration_seconds: float
    trace_path: str | None = None


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Structured outcome for one successfully executed workflow step."""

    step_id: str
    action: StepAction
    status: ActionStatus
    output_key: str | None = None
    duration_seconds: float = 0.0
    pages: tuple[TracePage, ...] = ()


@dataclass(frozen=True, slots=True)
class AssertionResult:
    """Structured outcome for one post-run assertion."""

    assertion_id: str
    assertion: str
    field: str
    status: AssertionStatus


@dataclass(frozen=True, slots=True)
class StepExecution:
    """Collected outputs and action outcomes from one workflow run."""

    outputs: dict[str, OutputValue]
    actions: tuple[ActionResult, ...]


@dataclass(frozen=True, slots=True)
class EngineResult:
    """Structured output from one isolated browser execution."""

    execution: StepExecution
    assertions: tuple[AssertionResult, ...]
    browser_version: str


class ReplayEngine(Protocol):
    """Async interface shared by CLI replay and future MCP callers."""

    async def run(
        self,
        workflow: Workflow,
        inputs: dict[str, object],
        failure_screenshot_path: Path | None = None,
    ) -> EngineResult:
        """Execute one validated workflow in an isolated browser context."""


class PlaywrightReplayEngine:
    """Run a validated workflow in a fresh, policy-bound Playwright context."""

    def __init__(
        self,
        overall_timeout_seconds: int = 300,
        playwright_factory: Callable[[], object] | None = None,
    ) -> None:
        if overall_timeout_seconds <= 0:
            raise ValueError("overall_timeout_seconds must be positive.")
        self.overall_timeout_seconds = overall_timeout_seconds
        self._playwright_factory = playwright_factory or async_playwright

    async def run(
        self,
        workflow: Workflow,
        inputs: dict[str, object],
        failure_screenshot_path: Path | None = None,
    ) -> EngineResult:
        """Bound the complete run while allowing cancellation to clean up normally."""

        validate_workflow_policy(workflow)
        # Preflight every navigation, including later steps, before launching Chromium.
        for step in workflow.steps:
            if step.action is StepAction.GOTO:
                for match in TEMPLATE_PATTERN.finditer(step.url or ""):
                    name = match.group(1)
                    if name not in workflow.inputs:
                        raise WorkflowExecutionError(
                            ExecutionFailureCategory.INPUT,
                            f"inputs.{name}",
                            "Input is not declared by the workflow.",
                        )
                url = render_url_template(step.url or "", inputs)
                violation = request_violation(
                    url, "GET", workflow.permissions.allowed_domains, navigation=True
                )
                if violation:
                    raise WorkflowExecutionError(
                        ExecutionFailureCategory.PERMISSION,
                        f"steps.{step.id}.url",
                        violation.message,
                    )
        try:
            async with asyncio.timeout(self.overall_timeout_seconds):
                return await self._run(workflow, inputs, failure_screenshot_path)
        except TimeoutError as error:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.RUNTIME,
                "replay",
                f"Workflow exceeded overall timeout of {self.overall_timeout_seconds} seconds."
                + (" Collection is incomplete." if any(s.paging for s in workflow.steps) else ""),
                actions=getattr(error.__cause__, "actions", ()),
                browser_version=getattr(error.__cause__, "browser_version", "unknown"),
            ) from error

    async def _run(
        self,
        workflow: Workflow,
        inputs: dict[str, object],
        failure_screenshot_path: Path | None,
    ) -> EngineResult:
        async with self._playwright_factory() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            browser_version = getattr(browser, "version", "unknown")
            try:
                boundary = BrowserBoundary(workflow.permissions.allowed_domains)
                context = await boundary.create_context(browser)
                try:
                    page = await context.new_page()
                    try:
                        execution = await execute_steps(
                            page,
                            workflow,
                            inputs,
                            allowed_domains=workflow.permissions.allowed_domains,
                            boundary=boundary,
                        )
                        execution = StepExecution(
                            process_outputs(workflow, execution.outputs), execution.actions
                        )
                        assertions = evaluate_success_assertions(
                            workflow.success, execution.outputs
                        )
                    except asyncio.CancelledError as error:
                        error.browser_version = browser_version
                        raise
                    except WorkflowExecutionError as error:
                        error.message = redact_text(error.message, workflow, inputs)
                        error.browser_version = browser_version
                        if not error.actions and "execution" in locals():
                            error.actions = execution.actions
                        # Screenshots cannot use structured-text redaction safely.
                        has_sensitive_input = any(
                            definition.sensitive and name in inputs
                            for name, definition in workflow.inputs.items()
                        )
                        if (
                            not has_sensitive_input
                            and not has_sensitive_outputs(workflow)
                            and boundary.failure is None
                            and error.category is not ExecutionFailureCategory.PERMISSION
                        ):
                            error.screenshot_path = await _capture_failure_screenshot(
                                page, failure_screenshot_path
                            )
                        raise
                    return EngineResult(execution, assertions, browser_version)
                finally:
                    boundary.closing = True
                    await context.close()
            finally:
                await browser.close()


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
    for step in workflow.steps:
        if step.paging and step.paging.max_pages_input:
            name = step.paging.max_pages_input
            value = parsed_inputs.setdefault(name, step.paging.max_pages)
            if type(value) is not int or not 1 <= value <= step.paging.max_pages:
                raise WorkflowExecutionError(
                    ExecutionFailureCategory.INPUT,
                    f"inputs.{name}",
                    f"Choose a whole page limit from 1 to {step.paging.max_pages}.",
                )
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


async def replay_workflow(
    loaded: LoadedWorkflow,
    input_pairs: list[str],
    trace_dir: Path | None = None,
    timeout_seconds: int = 300,
) -> ReplayResult:
    """Replay a workflow and optionally persist a redacted provenance trace."""

    validate_workflow_policy(loaded.workflow)
    run_id = uuid4().hex
    started_at = monotonic()
    started_at_utc = datetime.now(UTC)
    inputs: dict[str, object] = {}
    outputs: dict[str, OutputValue] = {}
    actions: tuple[ActionResult, ...] = ()
    assertions: tuple[AssertionResult, ...] = ()
    browser_version = "unknown"

    try:
        inputs = parse_input_pairs(input_pairs, loaded.workflow)
        engine = PlaywrightReplayEngine(overall_timeout_seconds=timeout_seconds)
        engine_result = await engine.run(
            loaded.workflow,
            inputs,
            _failure_screenshot_path(trace_dir, run_id),
        )
        browser_version = engine_result.browser_version
        outputs = engine_result.execution.outputs
        actions = engine_result.execution.actions
        assertions = engine_result.assertions
    except asyncio.CancelledError as error:
        execution_error = WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME,
            "replay",
            "Collection was cancelled and is incomplete; no results were returned.",
            actions=getattr(error, "actions", ()),
        )
        _persist_trace(
            trace_dir,
            run_id,
            loaded,
            started_at_utc,
            started_at,
            inputs,
            actions=execution_error.actions,
            outputs={},
            assertions=(),
            browser_version=getattr(error, "browser_version", browser_version),
            error=execution_error,
        )
        raise
    except WorkflowExecutionError as error:
        error.message = redact_text(error.message, loaded.workflow, inputs)
        error.trace_path = _persist_trace(
            trace_dir,
            run_id,
            loaded,
            started_at_utc,
            started_at,
            inputs,
            actions=error.actions,
            outputs=outputs,
            assertions=error.assertions,
            browser_version=error.browser_version
            if error.browser_version != "unknown"
            else browser_version,
            error=error,
        )
        raise
    except PlaywrightTimeoutError as error:
        execution_error = WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME,
            "replay",
            "Browser operation timed out.",
        )
        execution_error.trace_path = _persist_trace(
            trace_dir,
            run_id,
            loaded,
            started_at_utc,
            started_at,
            inputs,
            actions=actions,
            outputs=outputs,
            assertions=assertions,
            browser_version=browser_version,
            error=execution_error,
        )
        raise execution_error from error
    except Exception as error:
        execution_error = WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME,
            "replay",
            redact_text(_runtime_message(error), loaded.workflow, inputs),
        )
        execution_error.trace_path = _persist_trace(
            trace_dir,
            run_id,
            loaded,
            started_at_utc,
            started_at,
            inputs,
            actions=actions,
            outputs=outputs,
            assertions=assertions,
            browser_version=browser_version,
            error=execution_error,
        )
        raise execution_error from error

    trace_path = _persist_trace(
        trace_dir,
        run_id,
        loaded,
        started_at_utc,
        started_at,
        inputs,
        actions=actions,
        outputs=outputs,
        assertions=assertions,
        browser_version=browser_version,
    )
    return ReplayResult(
        workflow=loaded.workflow.name,
        manifest_digest=loaded.manifest_digest,
        outputs=outputs,
        actions=actions,
        duration_seconds=monotonic() - started_at,
        trace_path=trace_path,
    )


def _persist_trace(
    trace_dir: Path | None,
    run_id: str,
    loaded: LoadedWorkflow,
    started_at_utc: datetime,
    started_at: float,
    inputs: dict[str, object],
    *,
    actions: tuple[ActionResult, ...],
    outputs: dict[str, OutputValue],
    assertions: tuple[AssertionResult, ...],
    browser_version: str,
    error: WorkflowExecutionError | None = None,
) -> str | None:
    """Build and write one trace, returning its path when persistence is enabled."""

    if trace_dir is None:
        return None

    finished_at = datetime.now(UTC)
    trace = WorkflowTrace(
        schema_version=3
        if any(s.paging for s in loaded.workflow.steps)
        else 2
        if any(isinstance(d, StructuredOutput) for d in loaded.workflow.outputs.values())
        else 1,
        status=TraceStatus.FAILED if error else TraceStatus.COMPLETED,
        metadata=TraceMetadata(
            run_id=run_id,
            started_at=started_at_utc,
            finished_at=finished_at,
            duration_seconds=max(0.0, monotonic() - started_at),
            workflow_name=loaded.workflow.name,
            workflow_version=loaded.workflow.version,
            manifest_digest=loaded.manifest_digest,
            python_version=platform.python_version(),
            playwright_version=_package_version("playwright"),
            browser_name="chromium",
            browser_version=browser_version,
            platform=platform.platform(),
            publication=(
                {
                    "tool_id": loaded.publication.tool_id,
                    "version": loaded.publication.version,
                    "candidate_sha256": loaded.publication.candidate_sha256,
                }
                if loaded.publication
                else None
            ),
        ),
        inputs=redact_inputs(loaded.workflow, inputs),
        actions=[
            TraceAction(
                step_id=action.step_id,
                action=str(action.action),
                status=str(action.status),
                output_key=action.output_key,
                duration_seconds=max(0.0, action.duration_seconds),
                pages=list(action.pages),
            )
            for action in actions
        ],
        outputs=redact_outputs(outputs, loaded.workflow, inputs),
        assertions=[
            TraceAssertion(
                assertion_id=assertion.assertion_id,
                assertion=assertion.assertion,
                field=assertion.field,
                status=str(assertion.status),
            )
            for assertion in assertions
        ],
        error=(
            TraceError(
                category=str(error.category),
                path=error.path,
                message=redact_text(error.message, loaded.workflow, inputs),
                screenshot_path=error.screenshot_path,
            )
            if error
            else None
        ),
        screenshots=[error.screenshot_path] if error and error.screenshot_path else [],
    )
    return str(write_trace(trace, trace_dir, run_id))


def _failure_screenshot_path(trace_dir: Path | None, run_id: str) -> Path | None:
    if trace_dir is None:
        return None
    return trace_dir / f"{run_id}-failure.png"


async def _capture_failure_screenshot(page: Page, path: Path | None) -> str | None:
    """Capture a best-effort failure screenshot without hiding the original error."""

    if path is None:
        return None
    try:
        await page.screenshot(path=str(path), full_page=True)
    except Exception:
        return None
    return str(path)


def _package_version(package_name: str) -> str:
    try:
        return package_version(package_name)
    except PackageNotFoundError:
        return "unknown"


async def execute_steps(
    page: Page,
    workflow: Workflow,
    inputs: dict[str, object],
    *,
    allowed_domains: list[str] | None = None,
    boundary: BrowserBoundary | None = None,
) -> StepExecution:
    """Execute the declared read-only steps in their authored order."""

    outputs: dict[str, OutputValue] = {}
    actions: list[ActionResult] = []
    for step in workflow.steps:
        page_evidence: list[TracePage] = []
        output_key: str | None = step.id if step.id in workflow.outputs else None
        step_started_at = monotonic()
        try:
            _check_boundary(boundary, step.id)
            if boundary is not None:
                boundary.download_active = (
                    step.action is StepAction.DOWNLOAD and workflow.permissions.downloads
                )
            if step.action is StepAction.GOTO:
                url = render_url_template(step.url or "", inputs)
                violation = (
                    request_violation(url, "GET", allowed_domains, navigation=True)
                    if allowed_domains is not None
                    else None
                )
                if violation:
                    raise WorkflowExecutionError(
                        ExecutionFailureCategory.PERMISSION,
                        f"steps.{step.id}.url",
                        violation.message,
                    )
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=step.timeout_seconds * 1000,
                )
                if any(s.paging for s in workflow.steps) and (
                    response is None or not 200 <= response.status < 300
                ):
                    _check_boundary(boundary, step.id)
                    raise WorkflowExecutionError(
                        ExecutionFailureCategory.RUNTIME,
                        f"steps.{step.id}",
                        "The page returned an unsuccessful response. Collection is incomplete.",
                    )
            elif step.action is StepAction.FILL:
                await page.locator(render_template(step.selector or "", inputs)).fill(
                    render_template(step.value or "", inputs),
                    timeout=step.timeout_seconds * 1000,
                )
            elif step.action is StepAction.PRESS:
                key = render_template(step.key or "", inputs)
                if step.selector is not None:
                    await page.locator(render_template(step.selector, inputs)).press(
                        key,
                        timeout=step.timeout_seconds * 1000,
                    )
                else:
                    await asyncio.wait_for(
                        page.keyboard.press(key),
                        timeout=step.timeout_seconds,
                    )
            elif step.action is StepAction.CLICK:
                await page.locator(render_template(step.selector or "", inputs)).click(
                    timeout=step.timeout_seconds * 1000,
                )
            elif step.action is StepAction.WAIT_FOR_TEXT:
                await page.get_by_text(render_template(step.text or "", inputs)).first.wait_for(
                    state="visible",
                    timeout=step.timeout_seconds * 1000,
                )
            elif step.action is StepAction.EXTRACT_TEXT:
                extracted = await page.locator(
                    render_template(step.selector or "", inputs)
                ).inner_text(timeout=step.timeout_seconds * 1000)
                if output_key is not None:
                    outputs[output_key] = extracted
            elif step.action is StepAction.EXTRACT_RECORDS:
                if step.paging:
                    outputs[step.id], outputs[step.paging.status_output] = await collect_pages(
                        page,
                        step,
                        workflow.outputs[step.id],
                        allowed_domains or workflow.permissions.allowed_domains,
                        lambda: _check_boundary(boundary, step.id),
                        page_evidence,
                        inputs.get(step.paging.max_pages_input, step.paging.max_pages),
                    )
                else:
                    outputs[step.id] = await extract_records(page, step, workflow.outputs[step.id])
            elif step.action is StepAction.DOWNLOAD:
                async with page.expect_download(
                    timeout=step.timeout_seconds * 1000
                ) as download_info:
                    await page.locator(render_template(step.selector or "", inputs)).click(
                        timeout=step.timeout_seconds * 1000,
                    )
                download = await download_info.value
                if output_key is not None:
                    outputs[output_key] = download.suggested_filename
            else:
                raise WorkflowExecutionError(
                    ExecutionFailureCategory.RUNTIME,
                    f"steps.{step.id}",
                    f"Replay does not support action '{step.action}'.",
                )
            _check_boundary(boundary, step.id)
        except asyncio.CancelledError as error:
            error.actions = tuple(actions) + (
                ActionResult(
                    step_id=step.id,
                    action=step.action,
                    status=ActionStatus.FAILED,
                    output_key=output_key,
                    duration_seconds=monotonic() - step_started_at,
                    pages=tuple(page_evidence),
                ),
            )
            raise
        except OutputValidationError as error:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.PERMISSION
                if isinstance(error, PagingPermissionError)
                else ExecutionFailureCategory.ASSERTION,
                error.path,
                error.message + (" Collection is incomplete." if step.paging else ""),
                actions=tuple(actions)
                + (
                    ActionResult(
                        step_id=step.id,
                        action=step.action,
                        status=ActionStatus.FAILED,
                        output_key=output_key,
                        duration_seconds=monotonic() - step_started_at,
                        pages=tuple(page_evidence),
                    ),
                ),
            ) from None
        except WorkflowExecutionError as error:
            if step.paging:
                error.message += " Collection is incomplete; no results were returned."
            if not error.actions:
                error.actions = tuple(actions) + (
                    ActionResult(
                        step_id=step.id,
                        action=step.action,
                        status=ActionStatus.FAILED,
                        output_key=output_key,
                        duration_seconds=monotonic() - step_started_at,
                        pages=tuple(page_evidence),
                    ),
                )
            raise
        except Exception as error:
            violation = boundary.failure if boundary is not None else None
            if violation:
                category = (
                    ExecutionFailureCategory.RUNTIME
                    if violation.code == "network_error"
                    else ExecutionFailureCategory.PERMISSION
                )
                message = violation.message
            elif isinstance(error, (TimeoutError, PlaywrightTimeoutError)):
                category = ExecutionFailureCategory.RUNTIME
                message = f"Action '{step.action}' timed out after {step.timeout_seconds} seconds."
            else:
                category = ExecutionFailureCategory.RUNTIME
                message = _runtime_message(error)
            if step.paging:
                if violation:
                    message = violation.message
                elif isinstance(error, TimeoutError):
                    message = "Paging exceeded its total deadline."
                elif isinstance(error, PlaywrightTimeoutError):
                    message = "A paging browser operation timed out."
                else:
                    message = "A paging browser operation failed."
                message += " Collection is incomplete; no results were returned."
            raise WorkflowExecutionError(
                category,
                f"steps.{step.id}",
                message,
                actions=tuple(actions)
                + (
                    ActionResult(
                        step_id=step.id,
                        action=step.action,
                        status=ActionStatus.FAILED,
                        output_key=output_key,
                        duration_seconds=monotonic() - step_started_at,
                        pages=tuple(page_evidence),
                    ),
                ),
            ) from error
        finally:
            if boundary is not None:
                boundary.download_active = False

        actions.append(
            ActionResult(
                step_id=step.id,
                action=step.action,
                status=ActionStatus.COMPLETED,
                output_key=output_key,
                duration_seconds=monotonic() - step_started_at,
                pages=tuple(page_evidence),
            )
        )
    return StepExecution(outputs=outputs, actions=tuple(actions))


def _check_boundary(boundary: BrowserBoundary | None, step_id: str) -> None:
    violation: BrowserViolation | None = boundary.failure if boundary is not None else None
    if violation:
        raise WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME
            if violation.code == "network_error"
            else ExecutionFailureCategory.PERMISSION,
            f"steps.{step_id}",
            violation.message,
        )


def render_template(template: str, inputs: dict[str, object]) -> str:
    """Render declared workflow input placeholders without evaluating arbitrary expressions."""

    def replace(match: re.Match[str]) -> str:
        return _template_value(match.group(1), inputs)

    return TEMPLATE_PATTERN.sub(replace, template)


def render_url_template(template: str, inputs: dict[str, object]) -> str:
    """Encode raw inputs once as URL component data, preserving authored URL text."""

    parsed = validate_url_template(template)
    path_inputs = set(TEMPLATE_PATTERN.findall(parsed.path))

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        value = _template_value(name, inputs)
        # Chromium normalizes dot segments even when their dots are percent-encoded.
        if name in path_inputs and value in {"", ".", ".."}:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.INPUT,
                f"inputs.{name}",
                "A path-segment input cannot be empty, '.' or '..'.",
            )
        return quote(value, safe="")

    return TEMPLATE_PATTERN.sub(replace, template)


def _template_value(name: str, inputs: dict[str, object]) -> str:
    if name not in inputs:
        raise WorkflowExecutionError(
            ExecutionFailureCategory.INPUT,
            f"inputs.{name}",
            "Template references an input that was not provided.",
        )
    value = inputs[name]
    return str(value).lower() if isinstance(value, bool) else str(value)


def process_outputs(workflow: Workflow, raw_outputs: dict[str, object]) -> dict[str, OutputValue]:
    """Convert browser text once and validate the complete, closed output contract.

    Errors describe the expected type without echoing page values. Numbers use
    finite floats that retain the JSON decimal value when serialized. Overflow,
    underflow and precision loss are refused instead of silently changing data.
    """

    try:
        return validate_outputs(workflow, raw_outputs)
    except OutputValidationError as error:
        raise WorkflowExecutionError(
            ExecutionFailureCategory.ASSERTION, error.path, error.message
        ) from None


def evaluate_success_assertions(
    assertions: list[SuccessAssertion], outputs: dict[str, OutputValue]
) -> tuple[AssertionResult, ...]:
    """Verify V0 post-run output assertions before reporting replay success."""

    results: list[AssertionResult] = []
    for assertion in assertions:
        value = outputs.get(assertion.field)
        if assertion.assertion == "output_not_empty" and (
            value is None
            or isinstance(value, str)
            and not value.strip()
            or isinstance(value, (list, dict))
            and not value
        ):
            raise WorkflowExecutionError(
                ExecutionFailureCategory.ASSERTION,
                f"success.{assertion.id}",
                f"Output '{assertion.field}' is empty or was not extracted.",
                assertions=tuple(results)
                + (
                    AssertionResult(
                        assertion_id=assertion.id,
                        assertion=assertion.assertion,
                        field=assertion.field,
                        status=AssertionStatus.FAILED,
                    ),
                ),
            )
        if assertion.assertion == "output_matches_schema" and value is None:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.ASSERTION,
                f"success.{assertion.id}",
                "The required structured output was not extracted.",
            )
        results.append(
            AssertionResult(
                assertion_id=assertion.id,
                assertion=assertion.assertion,
                field=assertion.field,
                status=AssertionStatus.PASSED,
            )
        )
    return tuple(results)


def _runtime_message(error: Exception) -> str:
    message = str(error)
    if "Executable doesn't exist" in message:
        return "Chromium is not installed. Run 'uv run playwright install chromium'."
    return message or error.__class__.__name__
