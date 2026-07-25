"""End-to-end protocol coverage for the local STDIO MCP server."""

from __future__ import annotations

from mcp import ClientSession
from mcp_protocol_harness import run_protocol_test


def test_protocol_lists_expected_tool_schema() -> None:
    async def assert_tools(session: ClientSession) -> None:
        response = await session.list_tools()

        assert [tool.name for tool in response.tools] == ["hn_search"]
        tool = response.tools[0]
        assert tool.description is not None
        assert "Read-only reviewed workflow" in tool.description
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
        assert tool.outputSchema is not None
        assert tool.outputSchema["type"] == "object"
        assert tool.outputSchema["required"] == [
            "workflow",
            "manifest_digest",
            "outputs",
            "trace_path",
            "duration_seconds",
        ]
        assert tool.outputSchema["properties"]["outputs"]["$ref"] == (
            "#/$defs/HnSearchOutputs"
        )
        assert tool.outputSchema["$defs"]["HnSearchOutputs"]["properties"]["results"]["type"] == (
            "string"
        )

    run_protocol_test(assert_tools)


def test_protocol_returns_declared_output_and_provenance() -> None:
    async def assert_result(session: ClientSession) -> None:
        response = await session.call_tool("hn_search", {"query": "mcp"})

        assert response.isError is False
        assert response.structuredContent is not None
        assert response.structuredContent["workflow"] == "hn_search"
        assert response.structuredContent["outputs"] == {"results": "results for mcp"}
        assert response.structuredContent["trace_path"] == "traces/controlled-success.json"
        assert response.structuredContent["manifest_digest"].startswith("sha256:")

    run_protocol_test(assert_result)
