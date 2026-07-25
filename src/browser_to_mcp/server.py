"""Local STDIO MCP server for validated browser workflows."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.fastmcp.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, create_model

from browser_to_mcp.loader import LoadedWorkflow, load_workflow
from browser_to_mcp.models import Workflow
from browser_to_mcp.replay import (
    WorkflowExecutionError,
    parse_input_pairs,
    replay_workflow,
)
from browser_to_mcp.tracing import redact_text
from browser_to_mcp.validation import WorkflowValidationError, validate_workflow_policy

_INPUT_TYPES: dict[str, type[Any]] = {
    "string": StrictStr,
    "integer": StrictInt,
    "number": StrictFloat,
    "boolean": StrictBool,
}


class StrictArguments(ArgModelBase):
    """SDK argument model that rejects undeclared workflow inputs."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")


@dataclass(slots=True)
class ServerConfigurationError(Exception):
    """A startup configuration error that prevents partial registration."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def load_workflows(workflow_dir: Path) -> tuple[LoadedWorkflow, ...]:
    """Load every YAML workflow before registering any MCP tool."""

    if not workflow_dir.exists():
        raise ServerConfigurationError(str(workflow_dir), "Workflow directory does not exist.")
    if not workflow_dir.is_dir():
        raise ServerConfigurationError(str(workflow_dir), "Workflow path is not a directory.")

    try:
        workflow_paths = sorted(
            path
            for path in workflow_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
        )
    except OSError as error:
        raise ServerConfigurationError(str(workflow_dir), str(error)) from error

    if not workflow_paths:
        raise ServerConfigurationError(
            str(workflow_dir), "Workflow directory contains no YAML files."
        )

    loaded: list[LoadedWorkflow] = []
    errors: list[str] = []
    for path in workflow_paths:
        try:
            loaded.append(load_workflow(path))
        except WorkflowValidationError as error:
            errors.append(f"{error.path}: {error.message}")

    if errors:
        raise ServerConfigurationError(
            str(workflow_dir),
            "Workflow validation failed; no tools were registered. " + " | ".join(errors),
        )

    names: dict[str, Path] = {}
    duplicate_errors: list[str] = []
    for item, path in zip(loaded, workflow_paths, strict=True):
        previous = names.get(item.workflow.name)
        if previous is not None:
            duplicate_errors.append(
                f"Workflow name '{item.workflow.name}' is declared by both "
                f"'{previous}' and '{path}'."
            )
        names[item.workflow.name] = path
    if duplicate_errors:
        raise ServerConfigurationError(str(workflow_dir), " ".join(duplicate_errors))

    return tuple(loaded)


def create_server(
    workflows: Iterable[LoadedWorkflow],
    trace_dir: Path | None = Path("traces"),
    timeout_seconds: int = 300,
) -> FastMCP:
    """Create an MCP server containing only the supplied validated workflows."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")

    loaded_workflows = tuple(workflows)
    for item in loaded_workflows:
        try:
            validate_workflow_policy(item.workflow)
        except WorkflowValidationError as error:
            raise ServerConfigurationError(error.path, error.message) from error

    tools = [
        _workflow_tool(item, trace_dir=trace_dir, timeout_seconds=timeout_seconds)
        for item in loaded_workflows
    ]
    return FastMCP(
        name="browser-to-mcp",
        instructions=(
            "Expose reviewed, read-only browser workflows as typed local tools. "
            "Each tool replays its declared workflow and returns structured outputs "
            "with trace provenance."
        ),
        tools=tools,
    )


def serve_workflows(
    workflow_dir: Path,
    trace_dir: Path | None = Path("traces"),
    timeout_seconds: int = 300,
) -> None:
    """Load validated workflows and run the server over the STDIO transport."""

    workflows = load_workflows(workflow_dir)
    server = create_server(workflows, trace_dir=trace_dir, timeout_seconds=timeout_seconds)
    server.run(transport="stdio")


def _workflow_tool(
    loaded: LoadedWorkflow,
    *,
    trace_dir: Path | None,
    timeout_seconds: int,
) -> Tool:
    workflow = loaded.workflow

    async def invoke(**kwargs: object) -> CallToolResult:
        """Replay one validated workflow with protocol-safe structured results."""

        input_pairs = _input_pairs(workflow, kwargs)
        try:
            # Keep this preflight explicit so malformed values never start a browser.
            parse_input_pairs(input_pairs, workflow)
            result = await replay_workflow(
                loaded,
                input_pairs,
                trace_dir=trace_dir,
                timeout_seconds=timeout_seconds,
            )
        except WorkflowExecutionError as error:
            return _error_result(error, workflow, kwargs)

        payload = {
            "workflow": result.workflow,
            "manifest_digest": result.manifest_digest,
            "outputs": result.outputs,
            "trace_path": result.trace_path,
            "duration_seconds": round(result.duration_seconds, 3),
        }
        return _result(payload)

    invoke.__name__ = workflow.name
    invoke.__doc__ = workflow.description

    argument_model = _argument_model(workflow)
    metadata = FuncMetadata(arg_model=argument_model)
    return Tool(
        fn=invoke,
        name=workflow.name,
        description=_tool_description(workflow),
        parameters=argument_model.model_json_schema(by_alias=True),
        fn_metadata=metadata,
        is_async=True,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )


def _argument_model(workflow: Workflow) -> type[ArgModelBase]:
    fields: dict[str, tuple[Any, Any]] = {}
    for name, definition in workflow.inputs.items():
        annotation: Any = _INPUT_TYPES[definition.type]
        default: Any = ...
        if not definition.required:
            annotation = annotation | None
            default = None
        field_kwargs: dict[str, Any] = {}
        if definition.description is not None:
            field_kwargs["description"] = definition.description
        if definition.min_length is not None:
            field_kwargs["min_length"] = definition.min_length
        fields[name] = (annotation, Field(default=default, **field_kwargs))

    return create_model(
        f"{workflow.name.title().replace('_', '')}Arguments",
        __base__=StrictArguments,
        **fields,
    )


def _tool_description(workflow: Workflow) -> str:
    return (
        f"{workflow.description} Read-only reviewed workflow; "
        "no writes or side effects are allowed."
    )


def _input_pairs(workflow: Workflow, values: dict[str, object]) -> list[str]:
    pairs: list[str] = []
    for name in workflow.inputs:
        value = values.get(name)
        if value is None:
            continue
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        pairs.append(f"{name}={rendered}")
    return pairs


def _result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    text = json.dumps(payload, sort_keys=True)
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structuredContent=payload,
        isError=is_error,
    )


def _error_result(
    error: WorkflowExecutionError,
    workflow: Workflow,
    values: dict[str, object],
) -> CallToolResult:
    sensitive_inputs = {
        name: values[name]
        for name, definition in workflow.inputs.items()
        if definition.sensitive and name in values
    }
    payload = {
        "category": str(error.category),
        "path": error.path,
        "message": redact_text(error.message, workflow, sensitive_inputs),
        "trace_path": error.trace_path,
    }
    return _result(payload, is_error=True)
