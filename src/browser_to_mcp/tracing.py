"""Versioned, redacted workflow trace models and persistence."""

from __future__ import annotations

import json
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from browser_to_mcp.models import OutputValue, StructuredOutput, Workflow


class TraceStatus(StrEnum):
    """Terminal state recorded for a workflow run."""

    COMPLETED = "completed"
    FAILED = "failed"


class TraceError(BaseModel):
    """Stable execution error details included in a failed trace."""

    model_config = ConfigDict(extra="forbid")

    category: str
    path: str
    message: str
    screenshot_path: str | None = None


class TracePage(BaseModel):
    """Counts and outcomes only: no page addresses, identities, or cell contents."""

    model_config = ConfigDict(extra="forbid")
    page_number: int = Field(ge=1, le=20)
    rows_read: int = Field(ge=0, le=100)
    records_collected: int = Field(ge=0, le=100)
    outcome: Literal["next", "end", "page_limit", "record_limit", "failed", "cancelled"]
    duration_seconds: float = Field(ge=0)


class TraceAction(BaseModel):
    """One executed or failed browser action."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    action: str
    status: str
    output_key: str | None = None
    duration_seconds: float = Field(ge=0)
    pages: list[TracePage] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def serialize_action(self, handler):
        result = handler(self)
        if not self.pages:
            result.pop("pages", None)
        return result


class TraceAssertion(BaseModel):
    """One post-run assertion result."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: str
    assertion: str
    field: str
    status: str


class TraceMetadata(BaseModel):
    """Run and runtime provenance captured for every trace."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)
    workflow_name: str
    workflow_version: int
    manifest_digest: str
    python_version: str
    playwright_version: str
    browser_name: str
    browser_version: str
    platform: str
    publication: dict[str, str | int] | None = None
    evaluation_fixture_sha256: str | None = None


class WorkflowTrace(BaseModel):
    """Stable JSON document written for one workflow run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    status: TraceStatus
    metadata: TraceMetadata
    inputs: dict[str, Any]
    actions: list[TraceAction]
    outputs: dict[str, OutputValue]
    assertions: list[TraceAssertion]
    error: TraceError | None = None
    screenshots: list[str] = Field(default_factory=list)


def redact_inputs(workflow: Workflow, inputs: dict[str, object]) -> dict[str, Any]:
    """Return input values with fields marked sensitive replaced."""

    redacted: dict[str, Any] = {}
    for name, value in inputs.items():
        definition = workflow.inputs.get(name)
        redacted[name] = "[REDACTED]" if definition and definition.sensitive else value
    return redact_value(redacted, workflow, inputs)


def redact_text(value: str, workflow: Workflow, inputs: dict[str, object]) -> str:
    """Remove configured sensitive input values from diagnostic text."""

    return _redact_string(value, _sensitive_texts(workflow, inputs))


def _sensitive_texts(workflow: Workflow, inputs: dict[str, object]) -> list[str]:
    sensitive_values: set[str] = set()
    for name, definition in workflow.inputs.items():
        if definition.sensitive and name in inputs:
            value = inputs[name]
            sensitive_values.add(str(value))
            if isinstance(value, bool):
                sensitive_values.add(str(value).lower())
    return sorted(
        {text for value in sensitive_values if value for text in (value, quote(value, safe=""))},
        key=len,
        reverse=True,
    )


def _redact_string(value: str, sensitive_texts: list[str]) -> str:
    redacted = value
    for text in sensitive_texts:
        redacted = redacted.replace(text, "[REDACTED]")
    return redacted


def redact_value(value: Any, workflow: Workflow, inputs: dict[str, object]) -> Any:
    """Redact nested string values while preserving numeric and boolean types."""

    sensitive_texts = _sensitive_texts(workflow, inputs)

    def redact(item: Any) -> Any:
        if isinstance(item, str):
            return _redact_string(item, sensitive_texts)
        if isinstance(item, dict):
            return {key: redact(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [redact(child) for child in item]
        return item

    return redact(value)


def has_sensitive_outputs(workflow: Workflow) -> bool:
    return any(
        isinstance(definition, StructuredOutput)
        and (
            definition.sensitive
            or any(field.sensitive for field in definition.record_fields.values())
        )
        for definition in workflow.outputs.values()
    )


def redact_outputs(outputs, workflow: Workflow, inputs):
    result = redact_value(outputs, workflow, inputs)
    for name, definition in workflow.outputs.items():
        if name not in result or not isinstance(definition, StructuredOutput):
            continue
        if definition.sensitive:
            result[name] = "[REDACTED]"
            continue
        rows = result[name] if isinstance(result[name], list) else [result[name]]
        for row in rows:
            if isinstance(row, dict):
                for field, schema in definition.record_fields.items():
                    if schema.sensitive and field in row:
                        row[field] = "[REDACTED]"
    return result


def write_trace(trace: WorkflowTrace, trace_dir: Path, run_id: str) -> Path:
    """Write an indented UTF-8 trace document and return its path."""

    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / f"{run_id}.json"
    path.write_text(
        json.dumps(trace.model_dump(mode="json"), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return path
