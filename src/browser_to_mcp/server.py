"""Local STDIO MCP server for validated browser workflows."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.tools.base import Tool
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from browser_to_mcp.compiler import (
    CompiledWorkflow,
    SchemaCompilationError,
    compile_workflows,
)
from browser_to_mcp.loader import LoadedWorkflow, load_workflow
from browser_to_mcp.models import Workflow
from browser_to_mcp.replay import (
    WorkflowExecutionError,
    parse_input_pairs,
    replay_workflow,
)
from browser_to_mcp.tracing import redact_text
from browser_to_mcp.validation import WorkflowValidationError, validate_workflow_policy


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
    *,
    catalogs: Iterable[str] = (),
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

    try:
        compiled_workflows = compile_workflows(loaded_workflows)
    except SchemaCompilationError as error:
        raise ServerConfigurationError(error.path, error.message) from error

    tools = [
        _workflow_tool(item, trace_dir=trace_dir, timeout_seconds=timeout_seconds)
        for item in compiled_workflows
    ]
    if bindings := tuple(catalogs):
        from browser_to_mcp.catalog import catalog_tool

        tools.append(
            catalog_tool(
                compiled_workflows, bindings, trace_dir=trace_dir, timeout_seconds=timeout_seconds
            )
        )
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
    compiled: CompiledWorkflow,
    *,
    trace_dir: Path | None,
    timeout_seconds: int,
) -> Tool:
    loaded = compiled.loaded
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
            response = _error_result(error, workflow, kwargs)
            if loaded.publication:
                response.meta = {"publication": asdict(loaded.publication)}
            return response

        payload = {
            "workflow": result.workflow,
            "manifest_digest": result.manifest_digest,
            "outputs": result.outputs,
            "trace_path": result.trace_path,
            "duration_seconds": round(result.duration_seconds, 3),
        }
        response = _result(payload)
        if loaded.publication:
            response.meta = {"publication": asdict(loaded.publication)}
        return response

    invoke.__name__ = workflow.name
    invoke.__doc__ = workflow.description

    metadata = WorkflowFuncMetadata(
        arg_model=compiled.input_model,
        output_model=compiled.output_model,
        output_schema=compiled.output_schema,
    )
    return Tool(
        fn=invoke,
        name=workflow.name,
        description=compiled.description,
        parameters=compiled.input_schema,
        fn_metadata=metadata,
        is_async=True,
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )


class WorkflowFuncMetadata(FuncMetadata):
    """Validate successful structured results while preserving structured errors."""

    def convert_result(self, result: Any) -> Any:
        if isinstance(result, CallToolResult) and result.isError:
            return result
        return super().convert_result(result)


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
