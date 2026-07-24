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
from urllib.parse import urlparse
from uuid import uuid4

from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_to_mcp.loader import LoadedWorkflow
from browser_to_mcp.models import StepAction, SuccessAssertion, Workflow, WorkflowInput
from browser_to_mcp.tracing import (
    TraceAction,
    TraceAssertion,
    TraceError,
    TraceMetadata,
    TraceStatus,
    WorkflowTrace,
    redact_inputs,
    redact_text,
    write_trace,
)

_TEMPLATE_PATTERN = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


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
    outputs: dict[str, str]
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

    outputs: dict[str, str]
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

        try:
            return await asyncio.wait_for(
                self._run(workflow, inputs, failure_screenshot_path),
                timeout=self.overall_timeout_seconds,
            )
        except TimeoutError as error:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.RUNTIME,
                "replay",
                f"Workflow exceeded overall timeout of {self.overall_timeout_seconds} seconds.",
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
                context = await browser.new_context()
                try:
                    blocked_requests: list[str] = []

                    async def enforce_request_policy(route: object) -> None:
                        request = getattr(route, "request")
                        url = getattr(request, "url")
                        if _is_allowed_url(url, workflow.permissions.allowed_domains):
                            await route.continue_()
                            return
                        blocked_requests.append(url)
                        await route.abort()

                    await context.route("**/*", enforce_request_policy)
                    page = await context.new_page()
                    try:
                        execution = await execute_steps(
                            page,
                            workflow,
                            inputs,
                            allowed_domains=workflow.permissions.allowed_domains,
                            blocked_request_getter=lambda: (
                                blocked_requests[0] if blocked_requests else None
                            ),
                        )
                        assertions = evaluate_success_assertions(
                            workflow.success, execution.outputs
                        )
                    except WorkflowExecutionError as error:
                        error.browser_version = browser_version
                        if not error.actions and "execution" in locals():
                            error.actions = execution.actions
                        error.screenshot_path = await _capture_failure_screenshot(
                            page, failure_screenshot_path
                        )
                        raise
                    return EngineResult(execution, assertions, browser_version)
                finally:
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

    run_id = uuid4().hex
    started_at = monotonic()
    started_at_utc = datetime.now(UTC)
    inputs: dict[str, object] = {}
    outputs: dict[str, str] = {}
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
    except WorkflowExecutionError as error:
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
            _runtime_message(error),
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
    outputs: dict[str, str],
    assertions: tuple[AssertionResult, ...],
    browser_version: str,
    error: WorkflowExecutionError | None = None,
) -> str | None:
    """Build and write one trace, returning its path when persistence is enabled."""

    if trace_dir is None:
        return None

    finished_at = datetime.now(UTC)
    trace = WorkflowTrace(
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
        ),
        inputs=redact_inputs(loaded.workflow, inputs),
        actions=[
            TraceAction(
                step_id=action.step_id,
                action=str(action.action),
                status=str(action.status),
                output_key=action.output_key,
                duration_seconds=max(0.0, action.duration_seconds),
            )
            for action in actions
        ],
        outputs={
            key: redact_text(value, loaded.workflow, inputs) for key, value in outputs.items()
        },
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
    blocked_request_getter: Callable[[], str | None] | None = None,
) -> StepExecution:
    """Execute the declared read-only steps in their authored order."""

    outputs: dict[str, str] = {}
    actions: list[ActionResult] = []
    for step in workflow.steps:
        output_key: str | None = step.id if step.id in workflow.outputs else None
        step_started_at = monotonic()
        try:
            if step.action is StepAction.GOTO:
                url = render_template(step.url or "", inputs)
                if allowed_domains is not None and not _is_allowed_url(url, allowed_domains):
                    raise WorkflowExecutionError(
                        ExecutionFailureCategory.PERMISSION,
                        f"steps.{step.id}.url",
                        f"URL '{url}' is outside permissions.allowed_domains.",
                    )
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=step.timeout_seconds * 1000,
                )
            elif step.action is StepAction.FILL:
                await page.locator(render_template(step.selector or "", inputs)).fill(
                    render_template(step.value or "", inputs),
                    timeout=step.timeout_seconds * 1000,
                )
            elif step.action is StepAction.PRESS:
                await asyncio.wait_for(
                    page.keyboard.press(render_template(step.key or "", inputs)),
                    timeout=step.timeout_seconds,
                )
            elif step.action is StepAction.CLICK:
                await page.locator(render_template(step.selector or "", inputs)).click(
                    timeout=step.timeout_seconds * 1000,
                )
            elif step.action is StepAction.WAIT_FOR_TEXT:
                await page.get_by_text(render_template(step.text or "", inputs)).wait_for(
                    state="visible",
                    timeout=step.timeout_seconds * 1000,
                )
            elif step.action is StepAction.EXTRACT_TEXT:
                extracted = await page.locator(
                    render_template(step.selector or "", inputs)
                ).inner_text(timeout=step.timeout_seconds * 1000)
                if output_key is not None:
                    outputs[output_key] = extracted
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
            blocked_url = blocked_request_getter() if blocked_request_getter else None
            if blocked_url is not None:
                raise WorkflowExecutionError(
                    ExecutionFailureCategory.PERMISSION,
                    f"steps.{step.id}",
                    f"Request URL '{blocked_url}' is outside permissions.allowed_domains.",
                )
        except WorkflowExecutionError as error:
            if not error.actions:
                error.actions = tuple(actions) + (
                    ActionResult(
                        step_id=step.id,
                        action=step.action,
                        status=ActionStatus.FAILED,
                        output_key=output_key,
                        duration_seconds=monotonic() - step_started_at,
                    ),
                )
            raise
        except (TimeoutError, PlaywrightTimeoutError) as error:
            raise WorkflowExecutionError(
                ExecutionFailureCategory.RUNTIME,
                f"steps.{step.id}",
                f"Action '{step.action}' timed out after {step.timeout_seconds} seconds.",
                actions=tuple(actions)
                + (
                    ActionResult(
                        step_id=step.id,
                        action=step.action,
                        status=ActionStatus.FAILED,
                        output_key=output_key,
                        duration_seconds=monotonic() - step_started_at,
                    ),
                ),
            ) from error
        except Exception as error:
            blocked_url = blocked_request_getter() if blocked_request_getter else None
            if blocked_url is not None:
                raise WorkflowExecutionError(
                    ExecutionFailureCategory.PERMISSION,
                    f"steps.{step.id}",
                    f"Request URL '{blocked_url}' is outside permissions.allowed_domains.",
                    actions=tuple(actions)
                    + (
                        ActionResult(
                            step_id=step.id,
                            action=step.action,
                            status=ActionStatus.FAILED,
                            output_key=output_key,
                            duration_seconds=monotonic() - step_started_at,
                        ),
                    ),
                ) from error
            raise WorkflowExecutionError(
                ExecutionFailureCategory.RUNTIME,
                f"steps.{step.id}",
                _runtime_message(error),
                actions=tuple(actions)
                + (
                    ActionResult(
                        step_id=step.id,
                        action=step.action,
                        status=ActionStatus.FAILED,
                        output_key=output_key,
                        duration_seconds=monotonic() - step_started_at,
                    ),
                ),
            ) from error

        actions.append(
            ActionResult(
                step_id=step.id,
                action=step.action,
                status=ActionStatus.COMPLETED,
                output_key=output_key,
                duration_seconds=monotonic() - step_started_at,
            )
        )
    return StepExecution(outputs=outputs, actions=tuple(actions))


def _is_allowed_url(url: str, allowed_domains: list[str]) -> bool:
    """Allow declared hosts and browser-internal document URLs only."""

    parsed = urlparse(url)
    if parsed.scheme in {"about", "blob", "data"}:
        return True
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return False
    host = parsed.hostname.lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


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
) -> tuple[AssertionResult, ...]:
    """Verify V0 post-run output assertions before reporting replay success."""

    results: list[AssertionResult] = []
    for assertion in assertions:
        value = outputs.get(assertion.field)
        if assertion.assertion == "output_not_empty" and (value is None or not value.strip()):
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
