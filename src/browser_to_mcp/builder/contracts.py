"""User-owned task boundaries and the smaller, untrusted model proposal."""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal
from urllib.parse import parse_qsl, unquote, unquote_plus, urlsplit, urlunsplit

from pydantic import Field, StrictBool, ValidationError, model_validator

from browser_to_mcp.authoring.plan import bind_input, build_workflow, step_fields
from browser_to_mcp.browser_policy import SECRET_NAME, SECRET_VALUE, request_violation
from browser_to_mcp.jobs.candidate import ASSIGNMENT, MAX_SOURCE_BYTES, check_candidate
from browser_to_mcp.jobs.models import ClosedModel, JobError
from browser_to_mcp.models import (
    Paging,
    Permissions,
    RecordExtraction,
    RecordField,
    ScalarType,
    WorkflowInput,
    schema_field_name_error,
)

Limit = Annotated[int, Field(strict=True, ge=1, le=100)]


class TaskInput(ClosedModel):
    type: ScalarType = "string"
    description: str = Field(min_length=1, max_length=500)
    example: str | int | float | bool | None = None

    @model_validator(mode="after")
    def example_type(self):
        kinds = {"string": {str}, "integer": {int}, "number": {int, float}, "boolean": {bool}}
        if self.example is not None and type(self.example) not in kinds[self.type]:
            raise ValueError("Match the example to the declared input type.")
        return self


class TaskField(ClosedModel):
    type: ScalarType = "string"
    description: str = Field(min_length=1, max_length=500)
    # Link boundaries are granted by the user, never selected by the model.
    link_path_prefix: str | None = Field(default=None, max_length=512)


class TaskRequest(ClosedModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    start_url: str = Field(min_length=1, max_length=4096)
    description: str = Field(min_length=1, max_length=4000)
    allowed_domains: list[str] = Field(min_length=1, max_length=16)
    inputs: dict[str, TaskInput] = Field(max_length=16)
    fields: dict[str, TaskField] = Field(min_length=1, max_length=16)
    shape: Literal["object", "array"] | None = None
    max_items: Limit = 50
    max_pages: Annotated[int, Field(strict=True, ge=1, le=20)] = 1
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=120)] = 30
    allow_empty: StrictBool = False
    unique_by: str | None = None

    @model_validator(mode="after")
    def boundaries(self):
        if not self.description.strip():
            raise ValueError("Describe the task.")
        self.allowed_domains = Permissions(allowed_domains=self.allowed_domains).allowed_domains
        if request_violation(self.start_url, "GET", self.allowed_domains):
            raise ValueError("Choose a permitted public URL without credentials.")
        if "}}" in self.start_url or urlsplit(self.start_url).fragment:
            raise ValueError("Use a concrete starting URL without fragments or templates.")
        for name in [*self.inputs, *self.fields]:
            if len(name) > 64 or schema_field_name_error(name) or SECRET_NAME.search(name):
                raise ValueError("Use public input and output names.")
        if self.unique_by is not None and self.unique_by not in self.fields:
            raise ValueError("Choose a declared unique field.")
        for field in self.fields.values():
            if field.link_path_prefix is not None:
                prefix = field.link_path_prefix
                if (
                    field.type != "string"
                    or not prefix.startswith("/")
                    or prefix.startswith("//")
                    or any(c in prefix for c in "?#\\{}")
                    or any(part in {".", ".."} for part in prefix.split("/"))
                ):
                    raise ValueError("Use a fixed, absolute product-link path prefix.")
        if self.shape == "object" and (self.max_pages != 1 or self.allow_empty):
            raise ValueError("A single record cannot page or allow an empty list.")
        if self.shape is None and (self.max_pages > 1 or self.allow_empty):
            self.shape = "array"
        text = self.model_dump_json()
        if SECRET_VALUE.search(text) or ASSIGNMENT.search(text):
            raise ValueError("Supply public task data only.")
        return self


def check_task(value) -> tuple[TaskRequest, bytes]:
    try:
        task = TaskRequest.model_validate(value)
        data = json.dumps(
            task.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode()
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError
        return task, data
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise JobError("invalid_task") from None


class Binding(ClosedModel):
    location: Literal["query", "path"]
    # query key or zero-based path segment index (including the initial empty segment).
    key: str = Field(min_length=1, max_length=128)


class PageLinks(ClosedModel):
    next_selector: str = Field(min_length=1, max_length=512)
    end_selector: str = Field(min_length=1, max_length=512)
    page_parameter: str | None = None
    page_path: str | None = None


class Proposal(ClosedModel):
    status: Literal["draft", "unsupported"]
    reason: Literal["login", "challenge", "interaction", "ambiguous", "missing_data"] | None = None
    area: str | None = Field(default=None, min_length=1, max_length=512)
    record: RecordExtraction | None = None
    bindings: dict[str, Binding] = Field(default_factory=dict, max_length=16)
    paging: PageLinks | None = None


def proposal_schema():
    return Proposal.model_json_schema()


def _url_step(task, bindings):
    if set(bindings) != set(task.inputs):
        raise ValueError("Every input needs exactly one binding.")
    original = step_fields("goto", "open", url=task.start_url)
    result = original
    used = set()
    for name, binding in bindings.items():
        if binding.location == "query":
            parts = urlsplit(task.start_url).query.split("&")
            indexes = [
                i
                for i, part in enumerate(parts)
                if unquote_plus(part.partition("=")[0]) == binding.key
            ]
            if len(indexes) != 1 or "=" not in parts[indexes[0]]:
                raise ValueError
            field = f"query:{indexes[0]}"
            sample = parse_qsl(parts[indexes[0]], keep_blank_values=True)[0][1]
        else:
            if not re.fullmatch(r"[0-9]{1,3}", binding.key):
                raise ValueError
            index = int(binding.key)
            segments = urlsplit(task.start_url).path.split("/")
            if index >= len(segments) or not segments[index]:
                raise ValueError
            field, sample = f"path:{index}", unquote(segments[index])
        if field in used:
            raise ValueError("Inputs cannot share a URL position.")
        used.add(field)
        example = task.inputs[name].example
        if example is not None:
            expected = json.dumps(example) if not isinstance(example, str) else example
            if sample != expected:
                raise ValueError("The example must match the starting URL position.")
        result = bind_input(result, field, name)
    # Preserve every fixed byte in the starting URL; only declared values can change.
    parsed = urlsplit(result["url"])
    if urlunsplit(parsed) != result["url"]:
        raise ValueError
    return result


def make_candidate(task: TaskRequest, response: str):
    try:
        if not isinstance(response, str) or len(response.encode()) > 65536:
            raise ValueError
        plan = Proposal.model_validate_json(response)
        if plan.status == "unsupported":
            raise JobError("task_" + (plan.reason or "ambiguous"))
        if plan.reason is not None or not plan.area or not plan.record or task.shape is None:
            raise ValueError
        if set(plan.record.fields) != set(task.fields):
            raise ValueError
        if plan.record.unique_by != task.unique_by:
            raise ValueError
        expected_duplicates = "deduplicate" if task.unique_by else "reject"
        if plan.record.duplicates != expected_duplicates:
            raise ValueError
        if bool(plan.record.empty_selector) != task.allow_empty:
            raise ValueError
        for name, field in task.fields.items():
            extraction = plan.record.fields[name]
            if extraction.path_prefix != field.link_path_prefix:
                raise ValueError
            if (extraction.source == "href") != (field.link_path_prefix is not None):
                raise ValueError
        if bool(plan.paging) != (task.max_pages > 1):
            raise ValueError
        outputs = {"result": {"type": task.shape}}
        properties = {
            name: RecordField(type=field.type, description=field.description).model_dump()
            for name, field in task.fields.items()
        }
        if task.shape == "array":
            outputs["result"].update(
                items={"type": "object", "properties": properties},
                max_items=task.max_items,
                allow_empty=task.allow_empty,
            )
        else:
            outputs["result"]["properties"] = properties
        extraction = step_fields(
            "extract_records",
            "result",
            selector=plan.area,
            record=plan.record.model_dump(exclude_none=True),
            timeout_seconds=task.timeout_seconds,
        )
        if plan.paging:
            paging = Paging(
                **plan.paging.model_dump(exclude_none=True),
                max_pages=task.max_pages,
                timeout_seconds=task.timeout_seconds,
                status_output="collection",
            )
            # No declared input may also control the page counter.
            if any(
                b.location == "query" and b.key == paging.page_parameter
                for b in plan.bindings.values()
            ):
                raise ValueError
            extraction["paging"] = paging.model_dump(exclude_none=True)
            outputs["collection"] = {
                "type": "object",
                "properties": {
                    "pages_read": {"type": "integer"},
                    "complete": {"type": "boolean"},
                    "stop_reason": {"type": "string"},
                },
            }
        loaded = build_workflow(
            task.name,
            task.description,
            task.allowed_domains,
            [_url_step(task, plan.bindings), extraction],
            {
                name: WorkflowInput(type=field.type, description=field.description).model_dump(
                    exclude_none=True
                )
                for name, field in task.inputs.items()
            },
            outputs,
        )
        # The same normalization and credential checks as manually supplied job drafts.
        return check_candidate(
            json.dumps(loaded.workflow.model_dump(mode="json", exclude_none=True))
        )
    except JobError:
        raise
    except Exception:
        raise JobError("invalid_proposal") from None
