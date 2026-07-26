"""Command-line entry point for browser-to-mcp."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from browser_to_mcp.loader import load_workflow
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow
from browser_to_mcp.server import ServerConfigurationError, serve_workflows
from browser_to_mcp.validation import WorkflowValidationError
from browser_to_mcp.webmcp import discover_url


def build_parser() -> argparse.ArgumentParser:
    """Build the workflow validation and replay CLI parser."""

    parser = argparse.ArgumentParser(prog="browser-to-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="Validate a workflow YAML contract.")
    validate_parser.add_argument("workflow_path", type=Path)
    dry_run_parser = subparsers.add_parser(
        "dry-run", help="Print a validated workflow's replay plan without opening a browser."
    )
    dry_run_parser.add_argument("workflow_path", type=Path)
    replay_parser = subparsers.add_parser(
        "replay", help="Replay a supported read-only workflow in headless Chromium."
    )
    replay_parser.add_argument("workflow_path", type=Path)
    replay_parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Provide one declared workflow input. Repeat for multiple inputs.",
    )
    replay_parser.add_argument(
        "--trace-dir",
        type=Path,
        default=Path("traces"),
        help="Directory for redacted JSON traces and failure screenshots.",
    )
    replay_parser.add_argument(
        "--timeout-seconds",
        type=_positive_int,
        default=300,
        help="Maximum time allowed for the complete browser run.",
    )
    server_parser = subparsers.add_parser(
        "server", help="Serve validated workflows over a local MCP STDIO connection."
    )
    server_parser.add_argument(
        "--workflow-dir",
        type=Path,
        default=Path("workflows"),
        help="Directory containing YAML workflow contracts.",
    )
    server_parser.add_argument(
        "--trace-dir",
        type=Path,
        default=Path("traces"),
        help="Directory for redacted JSON traces and failure screenshots.",
    )
    server_parser.add_argument(
        "--timeout-seconds",
        type=_positive_int,
        default=300,
        help="Maximum time allowed for each browser run.",
    )
    discover_parser = subparsers.add_parser(
        "discover",
        help="Snapshot native WebMCP tools exposed by a page without invoking them.",
    )
    discover_parser.add_argument("url", help="Page URL to inspect.")
    discover_parser.add_argument(
        "--from-origin",
        action="append",
        default=[],
        dest="from_origins",
        metavar="ORIGIN",
        help="Request tools exposed to this origin. Repeat for multiple origins.",
    )
    discover_parser.add_argument(
        "--trace-dir",
        type=Path,
        default=Path("traces"),
        help="Directory for content-addressed native discovery snapshots.",
    )
    discover_parser.add_argument(
        "--timeout-seconds",
        type=_positive_int,
        default=30,
        help="Maximum time allowed for page load and discovery.",
    )
    return parser


def run(arguments: list[str] | None = None) -> int:
    """Run a CLI command and return a process exit code."""

    parser = build_parser()
    namespace = parser.parse_args(arguments)

    if namespace.command == "validate":
        return _validate(namespace.workflow_path)
    if namespace.command == "dry-run":
        return _dry_run(namespace.workflow_path)
    if namespace.command == "replay":
        return _replay(
            namespace.workflow_path,
            namespace.input,
            namespace.trace_dir,
            namespace.timeout_seconds,
        )
    if namespace.command == "server":
        return _server(namespace.workflow_dir, namespace.trace_dir, namespace.timeout_seconds)
    if namespace.command == "discover":
        return _discover(
            namespace.url,
            namespace.trace_dir,
            namespace.timeout_seconds,
            namespace.from_origins,
        )
    parser.error(f"Unsupported command: {namespace.command}")
    return 2


def _validate(path: Path) -> int:
    try:
        loaded = load_workflow(path)
    except WorkflowValidationError as error:
        _print_error("invalid", error.category, error.path, error.message)
        return 2

    print(
        json.dumps(
            {
                "status": "valid",
                "workflow": loaded.workflow.name,
                "manifest_digest": loaded.manifest_digest,
            }
        )
    )
    return 0


def _dry_run(path: Path) -> int:
    try:
        loaded = load_workflow(path)
    except WorkflowValidationError as error:
        _print_error("invalid", error.category, error.path, error.message)
        return 2

    workflow_contract = loaded.workflow.model_dump(mode="json")
    print(
        json.dumps(
            {
                "status": "planned",
                "workflow": loaded.workflow.name,
                "manifest_digest": loaded.manifest_digest,
                "allowed_domains": workflow_contract["permissions"]["allowed_domains"],
                "inputs": workflow_contract["inputs"],
                "steps": [
                    {
                        "id": step.id,
                        "action": step.action,
                        "timeout_seconds": step.timeout_seconds,
                        "risk": step.risk,
                        "idempotency": step.idempotency,
                        "retry": step.retry,
                        "confirmation": step.confirmation,
                    }
                    for step in loaded.workflow.steps
                ],
                "outputs": workflow_contract["outputs"],
                "success": workflow_contract["success"],
            }
        )
    )
    return 0


def _replay(
    path: Path,
    input_pairs: list[str],
    trace_dir: Path,
    timeout_seconds: int,
) -> int:
    try:
        loaded = load_workflow(path)
        result = asyncio.run(
            replay_workflow(loaded, input_pairs, trace_dir, timeout_seconds)
        )
    except WorkflowValidationError as error:
        _print_error("invalid", error.category, error.path, error.message)
        return 2
    except WorkflowExecutionError as error:
        _print_error(
            "failed",
            error.category,
            error.path,
            error.message,
            trace_path=error.trace_path,
        )
        return 2 if error.category.value == "input" else 1

    print(
        json.dumps(
            {
                "status": "completed",
                "workflow": result.workflow,
                "manifest_digest": result.manifest_digest,
                "outputs": result.outputs,
                "trace_path": result.trace_path,
                "actions": [
                    {
                        "step_id": action.step_id,
                        "action": action.action,
                        "status": action.status,
                        "output_key": action.output_key,
                    }
                    for action in result.actions
                ],
                "duration_seconds": round(result.duration_seconds, 3),
            }
        )
    )
    return 0


def _server(workflow_dir: Path, trace_dir: Path, timeout_seconds: int) -> int:
    try:
        serve_workflows(workflow_dir, trace_dir, timeout_seconds)
    except ServerConfigurationError as error:
        _print_error("invalid", "configuration", error.path, error.message)
        return 2
    return 0


def _discover(
    url: str,
    trace_dir: Path,
    timeout_seconds: int,
    from_origins: list[str],
) -> int:
    snapshot, trace_path = asyncio.run(
        discover_url(
            url,
            trace_dir=trace_dir,
            timeout_seconds=timeout_seconds,
            from_origins=from_origins,
        )
    )
    payload = snapshot.model_dump(mode="json")
    payload["trace_path"] = trace_path
    print(json.dumps(payload, sort_keys=True))
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _print_error(
    status: str,
    category: str,
    path: str,
    message: str,
    trace_path: str | None = None,
) -> None:
    """Write one structured command error to stderr."""

    payload = {
        "status": status,
        "category": category,
        "path": path,
        "message": message,
    }
    if trace_path is not None:
        payload["trace_path"] = trace_path
    print(
        json.dumps(payload),
        file=sys.stderr,
    )


def main() -> None:
    """Run the CLI as a console script."""

    raise SystemExit(run())
