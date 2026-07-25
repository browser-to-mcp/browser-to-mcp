"""Safety and error-path coverage for MCP protocol invocation."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession
from mcp_protocol_harness import WORKFLOW_PATH, run_protocol_test

from browser_to_mcp.server import ServerConfigurationError, load_workflows


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "Field required"),
        ({"query": ""}, "at least 1 character"),
        ({"query": 42}, "Input should be a valid string"),
        ({"query": "mcp", "extra": "rejected"}, "Extra inputs are not permitted"),
    ],
)
def test_protocol_rejects_invalid_input_before_replay(
    arguments: dict[str, Any], message: str
) -> None:
    async def assert_invalid_input(session: ClientSession) -> None:
        response = await session.call_tool("hn_search", arguments)

        assert response.isError is True
        assert response.structuredContent is None
        assert message in response.content[0].text

    run_protocol_test(assert_invalid_input)


def test_protocol_returns_stable_replay_failure_with_trace_reference() -> None:
    async def assert_failure(session: ClientSession) -> None:
        response = await session.call_tool("hn_search", {"query": "controlled_failure"})

        assert response.isError is True
        assert response.structuredContent == {
            "category": "runtime",
            "path": "steps.results",
            "message": "Controlled browser failure.",
            "trace_path": "traces/controlled-failure.json",
        }

    run_protocol_test(assert_failure)


def test_invalid_workflow_cannot_reach_protocol_tool_listing(tmp_path: Path) -> None:
    shutil.copy(WORKFLOW_PATH, tmp_path / "valid.yaml")
    (tmp_path / "unsafe.yaml").write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace("risk: read_only", "risk: financial", 1),
        encoding="utf-8",
    )

    with pytest.raises(ServerConfigurationError, match="no tools were registered"):
        load_workflows(tmp_path)
