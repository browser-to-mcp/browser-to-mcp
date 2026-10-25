"""Policy validation for workflow contracts before any browser is started."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from browser_to_mcp.browser_policy import request_violation
from browser_to_mcp.models import (
    Confirmation,
    Idempotency,
    Retry,
    Risk,
    StepAction,
    StructuredOutput,
    Workflow,
    schema_field_name_error,
)
from browser_to_mcp.url_templates import TEMPLATE_PATTERN, validate_url_template


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
        "requires_authentication",
    )
    for capability in disallowed_capabilities:
        if getattr(workflow.permissions, capability):
            raise WorkflowValidationError(
                FailureCategory.PERMISSION,
                f"permissions.{capability}",
                "V0 accepts only read-only capabilities.",
            )

    has_download_step = any(step.action is StepAction.DOWNLOAD for step in workflow.steps)
    if workflow.permissions.downloads and not has_download_step:
        raise WorkflowValidationError(
            FailureCategory.PERMISSION,
            "permissions.downloads",
            "Downloads permission is only valid for a download step.",
        )

    for step in workflow.steps:
        if step.risk is not Risk.READ_ONLY:
            raise WorkflowValidationError(
                FailureCategory.POLICY,
                f"steps.{step.id}.risk",
                "V0 accepts only read_only steps.",
            )
        # Preserve the more specific contradiction error before the V0 capability rule.
        if step.idempotency is Idempotency.NON_RETRYABLE and step.retry is not Retry.NONE:
            raise WorkflowValidationError(
                FailureCategory.POLICY,
                f"steps.{step.id}.retry",
                "A non_retryable step must declare retry: none.",
            )
        if step.retry is not Retry.NONE:
            raise WorkflowValidationError(
                FailureCategory.POLICY,
                f"steps.{step.id}.retry",
                "V0 does not support retries. Set retry to none.",
            )
        if step.confirmation is not Confirmation.NEVER:
            raise WorkflowValidationError(
                FailureCategory.POLICY,
                f"steps.{step.id}.confirmation",
                "V0 does not support confirmation-gated steps.",
            )
        if step.action is StepAction.DOWNLOAD and not workflow.permissions.downloads:
            raise WorkflowValidationError(
                FailureCategory.PERMISSION,
                f"steps.{step.id}",
                "A download step requires permissions.downloads to be true.",
            )
        if step.action is StepAction.GOTO:
            _validate_navigation(step.id, step.url, workflow.permissions.allowed_domains)

    producers = {step.id: step for step in workflow.steps}
    paging_status = _validate_paging_outputs(workflow, producers)
    for name, definition in workflow.outputs.items():
        name_error = schema_field_name_error(name)
        if name_error:
            raise WorkflowValidationError(FailureCategory.SCHEMA, f"outputs.{name}", name_error)
        if name in paging_status:
            continue
        producer = producers.get(name)
        if producer is None or producer.action not in {
            StepAction.EXTRACT_TEXT,
            StepAction.EXTRACT_RECORDS,
            StepAction.DOWNLOAD,
        }:
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                f"outputs.{name}",
                "Every output requires an extraction or download step with the same ID.",
            )
        if isinstance(definition, StructuredOutput):
            if producer.action is not StepAction.EXTRACT_RECORDS or producer.record is None:
                raise WorkflowValidationError(
                    FailureCategory.SCHEMA,
                    f"outputs.{name}",
                    "Structured outputs require an extract_records step with the same ID.",
                )
            if set(producer.record.fields) != set(definition.record_fields):
                raise WorkflowValidationError(
                    FailureCategory.SCHEMA,
                    f"steps.{name}.record.fields",
                    "Extracted fields must exactly match the record schema.",
                )
            for field, extraction in producer.record.fields.items():
                if extraction.source != "text" and definition.record_fields[field].type != "string":
                    raise WorkflowValidationError(
                        FailureCategory.SCHEMA,
                        f"steps.{name}.record.fields.{field}",
                        "Title attributes and links require string fields.",
                    )
            if producer.record.unique_by is not None and (
                definition.record_fields[producer.record.unique_by].nullable
            ):
                raise WorkflowValidationError(
                    FailureCategory.SCHEMA,
                    f"steps.{name}.record.unique_by",
                    "A record's unique key cannot be nullable.",
                )
            if definition.allow_empty:
                if not producer.record.empty_selector or not any(
                    a.field == name and a.assertion == "output_matches_schema"
                    for a in workflow.success
                ):
                    raise WorkflowValidationError(
                        FailureCategory.SCHEMA,
                        f"outputs.{name}.allow_empty",
                        "Empty lists need an empty_selector and an explicit schema success check.",
                    )
            elif producer.record.empty_selector:
                raise WorkflowValidationError(
                    FailureCategory.SCHEMA,
                    f"steps.{name}.record.empty_selector",
                    "An empty marker requires allow_empty on an array output.",
                )
        elif producer.action is StepAction.EXTRACT_RECORDS:
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                f"outputs.{name}",
                "An extract_records step requires an object or array output.",
            )
        if producer.action is StepAction.DOWNLOAD and definition.type != "string":
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                f"outputs.{name}",
                "A download step can produce only a string filename output.",
            )

    for step in workflow.steps:
        if step.action is StepAction.EXTRACT_RECORDS and step.id not in workflow.outputs:
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                f"steps.{step.id}",
                "Record extraction requires a declared output with the same ID.",
            )

    for assertion in workflow.success:
        if assertion.assertion == "output_matches_schema" and (
            assertion.field in workflow.outputs
            and not isinstance(workflow.outputs[assertion.field], StructuredOutput)
        ):
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                f"success.{assertion.id}",
                "output_matches_schema is for structured outputs only.",
            )
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

    try:
        validate_url_template(url)
    except ValueError as error:
        raise WorkflowValidationError(
            FailureCategory.SCHEMA,
            f"steps.{step_id}.url",
            str(error),
        ) from error

    violation = request_violation(
        TEMPLATE_PATTERN.sub("input", url), "GET", allowed_domains, navigation=True
    )
    if violation:
        raise WorkflowValidationError(
            FailureCategory.PERMISSION,
            f"steps.{step_id}.url",
            violation.message,
        )


def _validate_paging_outputs(workflow: Workflow, producers: dict) -> set[str]:
    statuses = set()
    expected = {"pages_read": "integer", "complete": "boolean", "stop_reason": "string"}
    for step in workflow.steps:
        if step.paging is None:
            continue
        path = f"steps.{step.id}.paging"
        output = workflow.outputs.get(step.id)
        if (
            not isinstance(output, StructuredOutput)
            or output.type != "array"
            or step.record is None
            or step.record.duplicates != "deduplicate"
            or step.record.unique_by is None
        ):
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                path,
                "Paging requires an array and deduplicate with an explicit stable unique_by key.",
            )
        name = step.paging.status_output
        status = workflow.outputs.get(name)
        if name in producers or name in statuses:
            raise WorkflowValidationError(
                FailureCategory.SCHEMA, path, "Paging status outputs must have one unique owner."
            )
        if (
            not isinstance(status, StructuredOutput)
            or status.type != "object"
            or {k: v.type for k, v in status.record_fields.items()} != expected
            or any(v.nullable for v in status.record_fields.values())
        ):
            raise WorkflowValidationError(
                FailureCategory.SCHEMA,
                path,
                "Declare status_output as an object with required pages_read (integer), "
                "complete (boolean), and stop_reason (string) fields.",
            )
        statuses.add(name)
    return statuses
