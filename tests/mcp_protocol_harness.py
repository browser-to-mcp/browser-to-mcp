"""Shared STDIO client harness for MCP protocol integration tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).parents[1]
WORKFLOW_PATH = ROOT / "workflows" / "hn_search.yaml"
FIXTURE_SERVER = ROOT / "tests" / "_mcp_protocol_fixture.py"


def run_protocol_test(
    assertion: Callable[[ClientSession], Awaitable[None]],
    workflow_dir: Path = WORKFLOW_PATH.parent,
) -> None:
    """Start the real STDIO transport and run one client assertion."""

    async def run() -> None:
        parameters = StdioServerParameters(
            command="uv",
            args=["run", "python", str(FIXTURE_SERVER), str(workflow_dir)],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await assertion(session)

    asyncio.run(run())
