"""Controlled STDIO server used by MCP protocol integration tests."""

from __future__ import annotations

import sys
from pathlib import Path

import browser_to_mcp.server as server_module
from browser_to_mcp.replay import (
    ExecutionFailureCategory,
    ReplayResult,
    WorkflowExecutionError,
    parse_input_pairs,
)


async def replay_fixture(
    loaded,
    input_pairs: list[str],
    trace_dir: Path | None,
    timeout_seconds: int,
) -> ReplayResult:
    """Return deterministic replay outcomes without launching a browser."""

    del trace_dir, timeout_seconds
    inputs = parse_input_pairs(input_pairs, loaded.workflow)
    query = str(inputs["query"])
    if query == "controlled_failure":
        raise WorkflowExecutionError(
            ExecutionFailureCategory.RUNTIME,
            "steps.results",
            "Controlled browser failure.",
            trace_path="traces/controlled-failure.json",
        )
    return ReplayResult(
        workflow=loaded.workflow.name,
        manifest_digest=loaded.manifest_digest,
        outputs={"results": f"results for {query}"},
        actions=(),
        duration_seconds=0.001,
        trace_path="traces/controlled-success.json",
    )


def main() -> None:
    """Run the controlled server for one protocol test process."""

    if len(sys.argv) != 2:
        raise SystemExit("Usage: _mcp_protocol_fixture.py WORKFLOW_DIRECTORY")
    server_module.replay_workflow = replay_fixture
    server = server_module.create_server(
        server_module.load_workflows(Path(sys.argv[1])),
        trace_dir=None,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
