"""Call a reviewed workflow through the local STDIO MCP server."""

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
    tool_name: str,
    input_pairs: list[str],
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
            tool = next((item for item in tool_result.tools if item.name == tool_name), None)
            if tool is None:
                available = ", ".join(item.name for item in tool_result.tools) or "none"
                raise RuntimeError(
                    f"The {tool_name} tool was not registered; available: {available}."
                )

            inputs = parse_tool_inputs(input_pairs, tool.inputSchema)
            call_result = await session.call_tool(tool_name, inputs)
            if call_result.isError:
                raise RuntimeError(f"The {tool_name} MCP call failed.")

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
    parser.add_argument(
        "--tool-name",
        default="hn_search",
        help="Generated MCP tool to call.",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Provide one tool input. Repeat for multiple inputs.",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Deprecated shorthand for --input query=VALUE.",
    )
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
    input_pairs = list(arguments.input)
    if arguments.query is not None:
        if any(pair.split("=", maxsplit=1)[0] == "query" for pair in input_pairs):
            raise SystemExit("Use either --query or --input query=VALUE, not both.")
        input_pairs.append(f"query={arguments.query}")
    payload = asyncio.run(
        run_demo(
            tool_name=arguments.tool_name,
            input_pairs=input_pairs,
            workflow_dir=arguments.workflow_dir,
            trace_dir=arguments.trace_dir,
            timeout_seconds=arguments.timeout_seconds,
        )
    )
    print(json.dumps(payload, indent=2, sort_keys=True))


def parse_tool_inputs(input_pairs: list[str], input_schema: dict[str, object]) -> dict[str, object]:
    """Parse CLI pairs using the generated MCP input schema."""

    properties = input_schema.get("properties")
    if not isinstance(properties, dict):
        raise RuntimeError("The selected tool has no object input schema.")

    values: dict[str, object] = {}
    for pair in input_pairs:
        if "=" not in pair:
            raise ValueError("Inputs must use NAME=VALUE format.")
        name, raw_value = pair.split("=", maxsplit=1)
        if not name:
            raise ValueError("Input names cannot be empty.")
        if name in values:
            raise ValueError(f"Input '{name}' was provided more than once.")
        definition = properties.get(name)
        if not isinstance(definition, dict):
            raise ValueError(f"Input '{name}' is not declared by the selected tool.")
        values[name] = _parse_tool_input(name, raw_value, definition)

    required = input_schema.get("required", [])
    if isinstance(required, list):
        missing = [name for name in required if isinstance(name, str) and name not in values]
        if missing:
            raise ValueError(f"Required input '{missing[0]}' was not provided.")
    return values


def _parse_tool_input(name: str, raw_value: str, definition: dict[str, object]) -> object:
    input_type = definition.get("type")
    if input_type == "string":
        return raw_value
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ValueError(f"Input '{name}' must be valid JSON for type '{input_type}'.") from error

    if input_type == "integer" and isinstance(value, int) and not isinstance(value, bool):
        return value
    if input_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if input_type == "boolean" and isinstance(value, bool):
        return value
    expected_container = list if input_type == "array" else dict
    if input_type in {"array", "object"} and isinstance(value, expected_container):
        return value
    raise ValueError(f"Input '{name}' must be a {input_type}.")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


if __name__ == "__main__":
    main()
