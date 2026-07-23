"""Pydantic models for reviewable browser workflow contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


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


class WorkflowInput(StrictModel):
    """Typed input accepted by a generated MCP tool."""

    type: Literal["string", "integer", "number", "boolean"]
    required: bool = True
    description: str | None = None
    min_length: PositiveInt | None = None


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
    timeout_seconds: PositiveInt = 30
    risk: Risk = Risk.READ_ONLY
    idempotency: Idempotency = Idempotency.SAFE


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
