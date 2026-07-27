"""Deterministic M3 readiness fixtures and repeatability measurements."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_to_mcp.models import (
    Confirmation,
    Idempotency,
    Manifest,
    Permissions,
    Retry,
    Risk,
    StepAction,
    SuccessAssertion,
    Workflow,
    WorkflowInput,
    WorkflowOutput,
    WorkflowStep,
)
from browser_to_mcp.replay import (
    ExecutionFailureCategory,
    WorkflowExecutionError,
    evaluate_success_assertions,
    execute_steps,
)
from browser_to_mcp.tracing import redact_inputs, redact_text
from browser_to_mcp.validation import WorkflowValidationError, validate_workflow_policy


class ReadinessCategory(StrEnum):
    """High-level outcomes used by the M3 readiness gate."""

    SUCCESS = "success"
    REFUSAL = "refusal"
    TIMEOUT = "timeout"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


@dataclass(frozen=True, slots=True)
class FixtureObservation:
    """One deterministic fixture outcome."""

    category: ReadinessCategory
    detail: str


@dataclass(frozen=True, slots=True)
class ReadinessFixture:
    """A scenario that must preserve one expected outcome across repeated runs."""

    name: str
    description: str
    expected_category: ReadinessCategory
    run: Callable[[], FixtureObservation]


@dataclass(frozen=True, slots=True)
class FixtureReport:
    """Aggregate measurements for one fixture."""

    name: str
    description: str
    expected_category: ReadinessCategory
    runs: int
    matching_runs: int
    classifications: dict[str, int]
    min_latency_ms: float
    mean_latency_ms: float
    max_latency_ms: float

    @property
    def success_rate(self) -> float:
        return self.matching_runs / self.runs

    @property
    def ready(self) -> bool:
        return self.matching_runs == self.runs

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe report record."""

        return {
            "name": self.name,
            "description": self.description,
            "expected_category": self.expected_category,
            "runs": self.runs,
            "matching_runs": self.matching_runs,
            "success_rate": round(self.success_rate, 3),
            "classifications": self.classifications,
            "latency_ms": {
                "min": round(self.min_latency_ms, 3),
                "mean": round(self.mean_latency_ms, 3),
                "max": round(self.max_latency_ms, 3),
            },
            "ready": self.ready,
        }


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """The complete M3 fixture measurement report."""

    runs_per_fixture: int
    fixtures: tuple[FixtureReport, ...]

    @property
    def ready(self) -> bool:
        return all(fixture.ready for fixture in self.fixtures)

    def to_dict(self) -> dict[str, object]:
        """Return the stable, JSON-safe report used by local and CI checks."""

        return {
            "status": "ready" if self.ready else "not_ready",
            "runs_per_fixture": self.runs_per_fixture,
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
        }


class _SelectorLocator:
    async def inner_text(self, timeout: int) -> str:
        del timeout
        raise PlaywrightError("Locator not found: main")


class _DelayedLocator:
    @property
    def first(self) -> _DelayedLocator:
        return self

    async def wait_for(self, state: str, timeout: int) -> None:
        del state, timeout
        raise PlaywrightTimeoutError("Timed out waiting for delayed content")


class _ChangedSelectorPage:
    def locator(self, selector: str) -> _SelectorLocator:
        assert selector == "main"
        return _SelectorLocator()


class _DelayedPage:
    def get_by_text(self, text: str) -> _DelayedLocator:
        assert text == "results"
        return _DelayedLocator()


def readiness_fixtures() -> tuple[ReadinessFixture, ...]:
    """Return the complete deterministic fixture matrix for the M3 gate."""

    return (
        ReadinessFixture(
            name="changed_selector",
            description="A changed page selector is a diagnosable runtime failure.",
            expected_category=ReadinessCategory.INFRASTRUCTURE_FAILURE,
            run=_changed_selector_fixture,
        ),
        ReadinessFixture(
            name="delayed_page",
            description="A page that misses its declared wait bound is a timeout.",
            expected_category=ReadinessCategory.TIMEOUT,
            run=_delayed_page_fixture,
        ),
        ReadinessFixture(
            name="malformed_output",
            description="Empty required output is refused by the success assertion.",
            expected_category=ReadinessCategory.REFUSAL,
            run=_malformed_output_fixture,
        ),
        ReadinessFixture(
            name="unauthorized_domain",
            description="Navigation outside the declared allowlist is refused before replay.",
            expected_category=ReadinessCategory.REFUSAL,
            run=_unauthorized_domain_fixture,
        ),
        ReadinessFixture(
            name="hostile_page_content",
            description="Hostile output remains data and sensitive values are redacted.",
            expected_category=ReadinessCategory.SUCCESS,
            run=_hostile_page_content_fixture,
        ),
    )


def run_readiness(
    runs_per_fixture: int = 10,
    fixtures: Sequence[ReadinessFixture] | None = None,
) -> ReadinessReport:
    """Run every deterministic fixture repeatedly and measure stable outcomes."""

    if runs_per_fixture <= 0:
        raise ValueError("runs_per_fixture must be positive.")

    reports: list[FixtureReport] = []
    for fixture in fixtures or readiness_fixtures():
        classifications: Counter[str] = Counter()
        latencies_ms: list[float] = []
        matching_runs = 0
        for _ in range(runs_per_fixture):
            started_at = perf_counter()
            try:
                observation = fixture.run()
            except Exception as error:
                observation = FixtureObservation(
                    ReadinessCategory.INFRASTRUCTURE_FAILURE,
                    error.__class__.__name__,
                )
            latencies_ms.append((perf_counter() - started_at) * 1000)
            classifications[str(observation.category)] += 1
            if observation.category is fixture.expected_category:
                matching_runs += 1

        reports.append(
            FixtureReport(
                name=fixture.name,
                description=fixture.description,
                expected_category=fixture.expected_category,
                runs=runs_per_fixture,
                matching_runs=matching_runs,
                classifications=dict(sorted(classifications.items())),
                min_latency_ms=min(latencies_ms),
                mean_latency_ms=sum(latencies_ms) / len(latencies_ms),
                max_latency_ms=max(latencies_ms),
            )
        )
    return ReadinessReport(runs_per_fixture, tuple(reports))


def _changed_selector_fixture() -> FixtureObservation:
    workflow = _workflow(
        steps=[_step("extract", StepAction.EXTRACT_TEXT, selector="main")],
    )
    try:
        asyncio.run(execute_steps(_ChangedSelectorPage(), workflow, {}))
    except WorkflowExecutionError as error:
        assert error.category is ExecutionFailureCategory.RUNTIME
        assert error.path == "steps.extract"
        return FixtureObservation(ReadinessCategory.INFRASTRUCTURE_FAILURE, error.message)
    raise AssertionError("Changed selector fixture unexpectedly completed.")


def _delayed_page_fixture() -> FixtureObservation:
    workflow = _workflow(
        steps=[_step("wait", StepAction.WAIT_FOR_TEXT, text="results", timeout_seconds=1)],
    )
    try:
        asyncio.run(execute_steps(_DelayedPage(), workflow, {}))
    except WorkflowExecutionError as error:
        assert error.category is ExecutionFailureCategory.RUNTIME
        assert "timed out" in error.message.lower()
        return FixtureObservation(ReadinessCategory.TIMEOUT, error.message)
    raise AssertionError("Delayed page fixture unexpectedly completed.")


def _malformed_output_fixture() -> FixtureObservation:
    workflow = _workflow(steps=[_step("extract", StepAction.EXTRACT_TEXT, selector="main")])
    try:
        evaluate_success_assertions(workflow.success, {"results": "   "})
    except WorkflowExecutionError as error:
        assert error.category is ExecutionFailureCategory.ASSERTION
        assert error.path == "success.results_present"
        return FixtureObservation(ReadinessCategory.REFUSAL, error.message)
    raise AssertionError("Malformed output fixture unexpectedly completed.")


def _unauthorized_domain_fixture() -> FixtureObservation:
    workflow = _workflow(
        steps=[_step("navigate", StepAction.GOTO, url="https://outside.test/")],
    )
    try:
        validate_workflow_policy(workflow)
    except WorkflowValidationError as error:
        assert error.path == "steps.navigate.url"
        return FixtureObservation(ReadinessCategory.REFUSAL, error.message)
    raise AssertionError("Unauthorized domain fixture unexpectedly completed.")


def _hostile_page_content_fixture() -> FixtureObservation:
    workflow = _workflow(
        steps=[_step("extract", StepAction.EXTRACT_TEXT, selector="main")],
        inputs={"query": WorkflowInput(type="string", sensitive=True, min_length=1)},
    )
    inputs = {"query": "private-value"}
    hostile_output = "<script>window.location='https://outside.test'</script> private-value"
    redacted_output = redact_text(hostile_output, workflow, inputs)

    assert redact_inputs(workflow, inputs) == {"query": "[REDACTED]"}
    assert "private-value" not in redacted_output
    assert "[REDACTED]" in redacted_output
    validate_workflow_policy(workflow)
    return FixtureObservation(
        ReadinessCategory.SUCCESS,
        "Hostile content was retained as redacted output data.",
    )


def _workflow(
    *,
    steps: list[WorkflowStep],
    inputs: dict[str, WorkflowInput] | None = None,
) -> Workflow:
    return Workflow(
        name="readiness_fixture",
        description="Deterministic M3 readiness fixture.",
        version=1,
        risk=Risk.READ_ONLY,
        manifest=Manifest(version=1),
        permissions=Permissions(allowed_domains=["example.test"]),
        inputs=inputs or {},
        steps=steps,
        outputs={"results": WorkflowOutput(type="string")},
        success=[
            SuccessAssertion(
                id="results_present",
                assertion="output_not_empty",
                field="results",
            )
        ],
    )


def _step(
    step_id: str,
    action: StepAction,
    *,
    url: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    timeout_seconds: int = 30,
) -> WorkflowStep:
    return WorkflowStep(
        id=step_id,
        action=action,
        url=url,
        selector=selector,
        text=text,
        timeout_seconds=timeout_seconds,
        risk=Risk.READ_ONLY,
        idempotency=Idempotency.SAFE,
        retry=Retry.NONE,
        confirmation=Confirmation.NEVER,
    )
