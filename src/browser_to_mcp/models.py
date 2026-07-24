"""Pydantic models for reviewable browser workflow contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator


class StrictModel(BaseModel):
    """Reject undeclared workflow fields so contracts remain reviewable."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Risk(StrEnum):
    READ_ONLY = "read_only"
    WRITE_WITH_REVIEW = "write_with_review"
    DESTRUCTIVE = "destructive"
    FINANCIAL = "financial"


class StepAction(StrEnum):
    GOTO = "goto"
    FILL = "fill"
    PRESS = "press"
    CLICK = "click"
    WAIT_FOR_TEXT = "wait_for_text"
    EXTRACT_TEXT = "extract_text"
    DOWNLOAD = "download"


class Idempotency(StrEnum):
    SAFE = "safe"
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class Retry(StrEnum):
    NONE = "none"
    ONE = "one"
    BOUNDED = "bounded"


class Confirmation(StrEnum):
    NEVER = "never"
    BEFORE_ACTION = "before_action"
    BEFORE_SIDE_EFFECT = "before_side_effect"


class Manifest(StrictModel):
    """Versioned metadata included in the canonical workflow digest."""

    version: PositiveInt


class Permissions(StrictModel):
    """Capabilities granted to a workflow before browser execution."""

    allowed_domains: list[str] = Field(min_length=1)
    downloads: bool = False
    uploads: bool = False
    submit_forms: bool = False
    send_messages: bool = False
    spend_money: bool = False

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, domains: list[str]) -> list[str]:
        """Accept hostnames only so domain policy comparisons are unambiguous."""

        normalized_domains: list[str] = []
        for domain in domains:
            normalized = domain.lower().rstrip(".")
            if "/" in normalized or ":" in normalized or "." not in normalized:
                raise ValueError(
                    "Allowed domains must be hostnames without a scheme, port, or path."
                )
            normalized_domains.append(normalized)
        return normalized_domains


class WorkflowInput(StrictModel):
    """Typed input accepted by a generated MCP tool."""

    type: Literal["string", "integer", "number", "boolean"]
    required: bool = True
    sensitive: bool = False
    description: str | None = None
    min_length: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_string_constraints(self) -> WorkflowInput:
        """Keep string-only constraints out of non-string MCP schemas."""

        if self.min_length is not None and self.type != "string":
            raise ValueError("min_length is only valid for string inputs.")
        return self


class WorkflowOutput(StrictModel):
    """Typed field returned from a workflow."""

    type: Literal["string", "integer", "number", "boolean"]
    description: str | None = None


class WorkflowStep(StrictModel):
    """One declared browser operation supported by the V0 contract."""

    id: str = Field(min_length=1)
    action: StepAction
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    key: str | None = None
    text: str | None = None
    timeout_seconds: PositiveInt = 30
    risk: Risk = Risk.READ_ONLY
    idempotency: Idempotency = Idempotency.SAFE
    retry: Retry = Retry.NONE
    confirmation: Confirmation = Confirmation.NEVER

    @model_validator(mode="after")
    def validate_action_fields(self) -> WorkflowStep:
        """Require the declared selectors and values needed by each V0 action."""

        requirements = {
            StepAction.GOTO: ("url",),
            StepAction.FILL: ("selector", "value"),
            StepAction.PRESS: ("key",),
            StepAction.CLICK: ("selector",),
            StepAction.WAIT_FOR_TEXT: ("text",),
            StepAction.EXTRACT_TEXT: ("selector",),
            StepAction.DOWNLOAD: ("selector",),
        }
        missing = [field for field in requirements[self.action] if not getattr(self, field)]
        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"Action '{self.action}' requires: {fields}.")
        return self


class SuccessAssertion(StrictModel):
    """A post-run assertion that must pass before reporting success."""

    id: str = Field(min_length=1)
    assertion: Literal["output_not_empty"]
    field: str = Field(min_length=1)


class Workflow(StrictModel):
    """Complete declarative contract compiled into a future MCP tool."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    description: str = Field(min_length=1)
    version: PositiveInt
    risk: Risk
    manifest: Manifest
    permissions: Permissions
    inputs: dict[str, WorkflowInput]
    steps: list[WorkflowStep] = Field(min_length=1)
    outputs: dict[str, WorkflowOutput] = Field(min_length=1)
    success: list[SuccessAssertion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_identifiers(self) -> Workflow:
        """Keep step and assertion references stable for traces and diagnostics."""

        _require_unique([step.id for step in self.steps], "steps", "step")
        _require_unique([assertion.id for assertion in self.success], "success", "assertion")
        return self


def _require_unique(values: list[str], path: str, noun: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{path} contains duplicate {noun} identifiers.")
