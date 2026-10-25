"""Pydantic models for reviewable browser workflow contracts."""

from __future__ import annotations

import keyword
import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StrictBool,
    field_validator,
    model_serializer,
    model_validator,
)

ScalarValue = str | int | float | bool
RecordValue = dict[str, ScalarValue | None]
OutputValue = ScalarValue | RecordValue | list[RecordValue]
ScalarType = Literal["string", "integer", "number", "boolean"]
MAX_RECORD_FIELDS = 16
MAX_RECORD_ROWS = 100
MAX_CELL_CHARS = 4096
MAX_RECORD_BYTES = 262144


def schema_field_name_error(name: str, model: type[BaseModel] = BaseModel) -> str | None:
    """Share field-name restrictions between workflow validation and MCP schemas."""

    if not name or not name.isidentifier() or keyword.iskeyword(name) or name.startswith("_"):
        return "Field names must be Python identifiers that are not keywords or private names."
    if hasattr(model, name):
        return f"Field name '{name}' conflicts with the schema model API."
    return None


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
    EXTRACT_RECORDS = "extract_records"
    DOWNLOAD = "download"


class Idempotency(StrEnum):
    SAFE = "safe"
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"


class Retry(StrEnum):
    """Stored retry vocabulary; V0 policy accepts only NONE."""

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
    requires_authentication: bool = False

    @field_validator("allowed_domains")
    @classmethod
    def normalize_domains(cls, domains: list[str]) -> list[str]:
        """Accept hostnames only so domain policy comparisons are unambiguous."""

        normalized_domains: list[str] = []
        for domain in domains:
            normalized = domain.lower().rstrip(".")
            label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            if not re.fullmatch(rf"{label}(?:\.{label})+", normalized):
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


class RecordField(StrictModel):
    """Every key is required; nullable explicitly permits missing/blank page values."""

    type: ScalarType
    description: str | None = None
    nullable: StrictBool = False
    sensitive: StrictBool = False


def validate_record_properties(properties: dict[str, RecordField]) -> dict[str, RecordField]:
    if not 1 <= len(properties) <= MAX_RECORD_FIELDS:
        raise ValueError(f"A record requires 1 to {MAX_RECORD_FIELDS} scalar fields.")
    for name in properties:
        if len(name) > 64 or schema_field_name_error(name):
            raise ValueError("Record fields need public identifiers of at most 64 characters.")
    return properties


class RecordShape(StrictModel):
    type: Literal["object"] = "object"
    properties: dict[str, RecordField]

    _properties = field_validator("properties")(validate_record_properties)


class StructuredOutput(StrictModel):
    """One record or a bounded list of records; nesting stops at scalar fields."""

    type: Literal["object", "array"]
    description: str | None = None
    properties: dict[str, RecordField] | None = None
    items: RecordShape | None = None
    max_items: Annotated[int, Field(strict=True, ge=1, le=MAX_RECORD_ROWS)] | None = None
    allow_empty: StrictBool = False
    sensitive: StrictBool = False

    @model_validator(mode="after")
    def validate_shape(self):
        if self.type == "object":
            if self.properties is None or self.items is not None or self.max_items is not None:
                raise ValueError("An object requires properties, without items or max_items.")
            if self.allow_empty:
                raise ValueError("A single record cannot allow an empty result.")
            validate_record_properties(self.properties)
        else:
            if self.items is None or self.properties is not None:
                raise ValueError("An array requires object items, without top-level properties.")
            if self.max_items is None:
                self.max_items = 50
        return self

    @property
    def record_fields(self) -> dict[str, RecordField]:
        return self.items.properties if self.items is not None else self.properties


OutputDefinition = Annotated[WorkflowOutput | StructuredOutput, Field(discriminator="type")]


class RecordExtractionField(StrictModel):
    selector: str = Field(min_length=1, max_length=512)
    source: Literal["text", "title", "href"] = "text"
    normalize_whitespace: StrictBool = True
    path_prefix: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_source(self):
        if self.source == "href":
            if "normalize_whitespace" in self.model_fields_set and self.normalize_whitespace:
                raise ValueError(
                    "Link values cannot normalize whitespace; preserve the browser URL."
                )
            self.normalize_whitespace = False
            if not self.path_prefix or not self.path_prefix.startswith("/"):
                raise ValueError("An href field requires a reviewed absolute path_prefix.")
            if any(c in self.path_prefix for c in ("?", "#", "\\")):
                raise ValueError("A link path_prefix cannot contain query, fragment, or backslash.")
        elif self.path_prefix is not None:
            raise ValueError("Only href fields can declare a path_prefix.")
        if "{{" in self.selector or "}}" in self.selector:
            raise ValueError("Record selectors must be fixed CSS, without templates.")
        return self


class RecordExtraction(StrictModel):
    rows: str = Field(min_length=1, max_length=512)
    fields: dict[str, RecordExtractionField] = Field(min_length=1, max_length=MAX_RECORD_FIELDS)
    empty_selector: str | None = Field(default=None, min_length=1, max_length=512)
    duplicates: Literal["reject", "keep", "deduplicate"] = "reject"
    unique_by: str | None = None

    @model_validator(mode="after")
    def validate_extraction(self):
        if any("{{" in text or "}}" in text for text in (self.rows, self.empty_selector or "")):
            raise ValueError("Record selectors must be fixed CSS, without templates.")
        if self.unique_by is not None and self.unique_by not in self.fields:
            raise ValueError("unique_by must name an extracted field.")
        if self.duplicates == "deduplicate" and self.unique_by is None:
            raise ValueError("Deduplication requires an explicit unique_by field.")
        if self.duplicates == "keep" and self.unique_by is not None:
            raise ValueError("Keeping duplicates cannot also declare a unique key.")
        return self


class Paging(StrictModel):
    """Follow reviewed numbered Next links, with explicit end and finite budgets."""

    next_selector: str = Field(min_length=1, max_length=512)
    end_selector: str = Field(min_length=1, max_length=512)
    page_parameter: str | None = Field(
        default=None, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_]*$"
    )
    page_path: str | None = Field(default=None, max_length=512)
    max_pages: Annotated[int, Field(strict=True, ge=1, le=20)]
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=300)]
    status_output: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_paging(self):
        if (self.page_parameter is None) == (self.page_path is None):
            raise ValueError("Paging requires exactly one of page_parameter or page_path.")
        if self.page_path is not None and (
            not self.page_path.startswith("/")
            or self.page_path.startswith("//")
            or self.page_path.count("{page}") != 1
            or any(c in self.page_path.replace("{page}", "") for c in "{}?#\\")
            or any(part in {".", ".."} for part in self.page_path.split("/"))
        ):
            raise ValueError("page_path must be an absolute path with one {page} placeholder.")
        if any("{{" in s or "}}" in s for s in (self.next_selector, self.end_selector)):
            raise ValueError("Paging selectors must be fixed CSS, without templates.")
        if schema_field_name_error(self.status_output):
            raise ValueError("Paging status_output requires a public output name.")
        return self


class WorkflowStep(StrictModel):
    """One declared browser operation supported by the V0 contract."""

    id: str = Field(min_length=1)
    action: StepAction
    url: str | None = None
    selector: str | None = None
    value: str | None = None
    key: str | None = None
    text: str | None = None
    record: RecordExtraction | None = None
    paging: Paging | None = None
    timeout_seconds: PositiveInt
    risk: Risk
    idempotency: Idempotency
    retry: Retry
    confirmation: Confirmation

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
            StepAction.EXTRACT_RECORDS: ("selector", "record"),
            StepAction.DOWNLOAD: ("selector",),
        }
        missing = [field for field in requirements[self.action] if not getattr(self, field)]
        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"Action '{self.action}' requires: {fields}.")
        if self.action is not StepAction.EXTRACT_RECORDS and self.record is not None:
            raise ValueError("Only extract_records can declare record extraction settings.")
        if self.paging is not None and self.action is not StepAction.EXTRACT_RECORDS:
            raise ValueError("Only extract_records can declare paging settings.")
        if self.action is StepAction.EXTRACT_RECORDS and (
            not self.selector
            or len(self.selector) > 512
            or "{{" in self.selector
            or "}}" in self.selector
        ):
            raise ValueError(
                "A records area requires a fixed CSS selector of at most 512 characters."
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_step(self, handler):
        result = handler(self)
        if self.record is None:
            result.pop("record", None)  # Existing scalar manifests retain their identity.
        if self.paging is None:
            result.pop("paging", None)
        return result


class SuccessAssertion(StrictModel):
    """A post-run assertion that must pass before reporting success."""

    id: str = Field(min_length=1)
    assertion: Literal["output_not_empty", "output_matches_schema"]
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
    outputs: dict[str, OutputDefinition] = Field(min_length=1)
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
