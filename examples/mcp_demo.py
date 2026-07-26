"""Call the reference workflow through the local STDIO MCP server."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW_DIR = ROOT / "workflows"


async def run_demo(
    *,
    query: str,
    workflow_dir: Path,
    trace_dir: Path,
    timeout_seconds: int,
) -> dict[str, object]:
    """List the generated tool, call it, and return a JSON-safe demonstration payload."""

    server_parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "from browser_to_mcp.cli import main; main()",
            "server",
            "--workflow-dir",
            str(workflow_dir.resolve()),
            "--trace-dir",
            str(trace_dir.resolve()),
            "--timeout-seconds",
            str(timeout_seconds),
        ],
    )
    async with stdio_client(server_parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_result = await session.list_tools()
            tool = next((item for item in tool_result.tools if item.name == "hn_search"), None)
            if tool is None:
                available = ", ".join(item.name for item in tool_result.tools) or "none"
                raise RuntimeError(
                    f"The hn_search tool was not registered; available: {available}."
                )

            call_result = await session.call_tool("hn_search", {"query": query})
            if call_result.isError:
                raise RuntimeError("The hn_search MCP call failed.")

            return {
                "tool": {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                    "output_schema": tool.outputSchema,
                },
                "call": call_result.structuredContent,
            }


def build_parser() -> argparse.ArgumentParser:
    """Build the reproducible reference-demo parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="python", help="Hacker News search query.")
    parser.add_argument(
        "--workflow-dir",
        type=Path,
        default=DEFAULT_WORKFLOW_DIR,
        help="Directory containing workflow YAML files.",
    )
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=ROOT / "traces",
        help="Directory for the replay trace.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_int,
        default=300,
        help="Maximum time allowed for the MCP call.",
    )
    return parser


def main() -> None:
    """Run the reference MCP demonstration."""

    arguments = build_parser().parse_args()
    payload = asyncio.run(
        run_demo(
            query=arguments.query,
            workflow_dir=arguments.workflow_dir,
            trace_dir=arguments.trace_dir,
            timeout_seconds=arguments.timeout_seconds,
        )
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


if __name__ == "__main__":
    main()
