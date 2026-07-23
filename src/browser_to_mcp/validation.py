"""Policy validation for workflow contracts before any browser is started."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from browser_to_mcp.models import Idempotency, Retry, Risk, StepAction, Workflow


class FailureCategory(StrEnum):
    SCHEMA = "schema"
    PERMISSION = "permission"
    POLICY = "policy"


@dataclass(slots=True)
class WorkflowValidationError(Exception):
    """A stable validation failure surfaced by the CLI and later MCP server."""

    category: FailureCategory
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.category}: {self.path}: {self.message}"


def validate_workflow_policy(workflow: Workflow) -> None:
    """Reject incomplete or non-read-only contracts before execution exists."""

    if workflow.risk is not Risk.READ_ONLY:
        raise WorkflowValidationError(
            FailureCategory.POLICY,
            "risk",
            "V0 accepts only read_only workflows.",
        )

    disallowed_capabilities = (
        "uploads",
        "submit_forms",
        "send_messages",
        "spend_money",
    )
    for capability in disallowed_capabilities:
        if getattr(workflow.permissions, capability):
            raise WorkflowValidationError(
                FailureCategory.PERMISSION,
                f"permissions.{capability}",
                "V0 accepts only read-only capabilities.",
            )

    for step in workflow.steps:
        if step.risk is not Risk.READ_ONLY:
            raise WorkflowValidationError(
                FailureCategory.POLICY,
                f"steps.{step.id}.risk",
                "V0 accepts only read_only steps.",
            )
        if step.idempotency is Idempotency.NON_RETRYABLE and step.retry is not Retry.NONE:
            raise WorkflowValidationError(
                FailureCategory.POLICY,
                f"steps.{step.id}.retry",
                "A non_retryable step must declare retry: none.",
            )
        if step.action is StepAction.DOWNLOAD and not workflow.permissions.downloads:
            raise WorkflowValidationError(
                FailureCategory.PERMISSION,
                f"steps.{step.id}",
                "A download step requires permissions.downloads to be true.",
            )
        if step.action is StepAction.GOTO:
            _validate_navigation(step.id, step.url, workflow.permissions.allowed_domains)

    for assertion in workflow.success:
        if assertion.field not in workflow.outputs:
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                f"success.{assertion.id}.field",
                f"Output field '{assertion.field}' is not declared.",
            )


def _validate_navigation(step_id: str, url: str | None, allowed_domains: list[str]) -> None:
    if not url:
        raise WorkflowValidationError(
            FailureCategory.SCHEMA,
            f"steps.{step_id}.url",
            "A goto step requires a URL.",
        )

    host = urlparse(url).hostname
    if not host:
        raise WorkflowValidationError(
            FailureCategory.SCHEMA,
            f"steps.{step_id}.url",
            "A goto step requires an absolute URL with a hostname.",
        )

    if not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
        raise WorkflowValidationError(
            FailureCategory.PERMISSION,
            f"steps.{step_id}.url",
            f"Domain '{host}' is not in permissions.allowed_domains.",
        )
