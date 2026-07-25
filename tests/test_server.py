from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

import browser_to_mcp.server as server_module
from browser_to_mcp.loader import LoadedWorkflow, load_workflow, manifest_digest
from browser_to_mcp.models import Risk, WorkflowInput
from browser_to_mcp.replay import ReplayResult, WorkflowExecutionError
from browser_to_mcp.server import (
    ServerConfigurationError,
    create_server,
    load_workflows,
)

WORKFLOW_PATH = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


def test_load_workflows_requires_a_complete_valid_directory(tmp_path: Path) -> None:
    with pytest.raises(ServerConfigurationError, match="does not exist"):
        load_workflows(tmp_path / "missing")

    with pytest.raises(ServerConfigurationError, match="no YAML files"):
        load_workflows(tmp_path)

    shutil.copy(WORKFLOW_PATH, tmp_path / "valid.yaml")
    (tmp_path / "invalid.yaml").write_text("name: [", encoding="utf-8")
    with pytest.raises(ServerConfigurationError, match="no tools were registered"):
        load_workflows(tmp_path)


def test_load_workflows_rejects_duplicate_tool_names(tmp_path: Path) -> None:
    shutil.copy(WORKFLOW_PATH, tmp_path / "first.yaml")
    shutil.copy(WORKFLOW_PATH, tmp_path / "second.yaml")

    with pytest.raises(ServerConfigurationError, match="declared by both"):
        load_workflows(tmp_path)


def test_create_server_rejects_unsafe_workflows() -> None:
    source = load_workflow(WORKFLOW_PATH)
    unsafe = source.workflow.model_copy(update={"risk": Risk.FINANCIAL})
    loaded = LoadedWorkflow(unsafe, manifest_digest(unsafe))

    with pytest.raises(ServerConfigurationError, match="read_only"):
        create_server([loaded])


def test_create_server_rejects_compiler_name_collisions() -> None:
    loaded = load_workflow(WORKFLOW_PATH)

    with pytest.raises(ServerConfigurationError, match="Tool name 'hn_search'"):
        create_server([loaded, loaded])


def test_server_lists_typed_read_only_workflow_tools() -> None:
    server = create_server(load_workflows(WORKFLOW_PATH.parent))

    tools = asyncio.run(server.list_tools())

    assert [tool.name for tool in tools] == ["hn_search"]
    tool = tools[0]
    assert tool.inputSchema == {
        "additionalProperties": False,
        "properties": {
            "query": {
                "description": "Search query.",
                "minLength": 1,
                "title": "Query",
                "type": "string",
            }
        },
        "required": ["query"],
        "title": "HnSearchArguments",
        "type": "object",
    }
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert "Read-only reviewed workflow" in (tool.description or "")


def test_server_lists_declared_output_schema() -> None:
    server = create_server(load_workflows(WORKFLOW_PATH.parent))

    tools = asyncio.run(server.list_tools())

    assert tools[0].outputSchema is not None
    assert tools[0].outputSchema["properties"]["workflow"] == {
        "const": "hn_search",
        "title": "Workflow",
        "type": "string",
    }
    assert tools[0].outputSchema["properties"]["manifest_digest"]["const"].startswith(
        "sha256:"
    )
    assert tools[0].outputSchema["properties"]["outputs"]["$ref"].endswith("Outputs")


def test_server_exposes_workflow_over_stdio() -> None:
    async def inspect_server() -> list[str]:
        parameters = StdioServerParameters(
            command="uv",
            args=[
                "run",
                "browser-to-mcp",
                "server",
                "--workflow-dir",
                str(WORKFLOW_PATH.parent),
            ],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                return [tool.name for tool in response.tools]

    assert asyncio.run(inspect_server()) == ["hn_search"]


def test_server_invocation_replays_and_returns_structured_provenance(monkeypatch) -> None:
    received: dict[str, object] = {}

    async def fake_replay(loaded, input_pairs, trace_dir, timeout_seconds):
        received.update(
            input_pairs=input_pairs,
            trace_dir=trace_dir,
            timeout_seconds=timeout_seconds,
        )
        return ReplayResult(
            workflow=loaded.workflow.name,
            manifest_digest=loaded.manifest_digest,
            outputs={"results": "visible results"},
            actions=(),
            duration_seconds=0.125,
            trace_path="traces/run.json",
        )

    monkeypatch.setattr(server_module, "replay_workflow", fake_replay)
    server = create_server(load_workflows(WORKFLOW_PATH.parent), timeout_seconds=12)

    result = asyncio.run(server.call_tool("hn_search", {"query": "mcp"}))

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["outputs"] == {"results": "visible results"}
    assert result.structuredContent["trace_path"] == "traces/run.json"
    assert received == {
        "input_pairs": ["query=mcp"],
        "trace_dir": Path("traces"),
        "timeout_seconds": 12,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"query": ""},
        {"query": 42},
        {"query": "mcp", "extra": "rejected"},
    ],
)
def test_server_rejects_invalid_inputs_before_invocation(arguments: dict[str, object]) -> None:
    server = create_server(load_workflows(WORKFLOW_PATH.parent))

    with pytest.raises(ToolError):
        asyncio.run(server.call_tool("hn_search", arguments))


def test_server_returns_redacted_structured_replay_errors(monkeypatch) -> None:
    source = load_workflow(WORKFLOW_PATH)
    workflow = source.workflow.model_copy(
        update={
            "inputs": {
                "query": WorkflowInput(type="string", sensitive=True, min_length=1)
            }
        }
    )
    loaded = LoadedWorkflow(workflow, manifest_digest(workflow))

    async def fake_replay(*args, **kwargs):
        raise WorkflowExecutionError(
            category="runtime",
            path="steps.search",
            message="browser failed while processing secret",
            trace_path="traces/failure.json",
        )

    monkeypatch.setattr(server_module, "replay_workflow", fake_replay)
    server = create_server([loaded])

    result = asyncio.run(server.call_tool("hn_search", {"query": "secret"}))

    assert result.isError is True
    assert result.structuredContent == {
        "category": "runtime",
        "path": "steps.search",
        "message": "browser failed while processing [REDACTED]",
        "trace_path": "traces/failure.json",
    }
    assert "secret" not in json.dumps(result.structuredContent)


def test_server_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        create_server(load_workflows(WORKFLOW_PATH.parent), timeout_seconds=0)
