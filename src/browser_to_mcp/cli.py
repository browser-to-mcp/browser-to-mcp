"""Command-line entry point for browser-to-mcp."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from browser_to_mcp.loader import load_workflow
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow
from browser_to_mcp.validation import WorkflowValidationError


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
        return _replay(namespace.workflow_path, namespace.input)
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
                    }
                    for step in loaded.workflow.steps
                ],
                "outputs": workflow_contract["outputs"],
                "success": workflow_contract["success"],
            }
        )
    )
    return 0


def _replay(path: Path, input_pairs: list[str]) -> int:
    try:
        loaded = load_workflow(path)
        result = asyncio.run(replay_workflow(loaded, input_pairs))
    except WorkflowValidationError as error:
        _print_error("invalid", error.category, error.path, error.message)
        return 2
    except WorkflowExecutionError as error:
        _print_error("failed", error.category, error.path, error.message)
        return 2 if error.category.value == "input" else 1

    print(
        json.dumps(
            {
                "status": "completed",
                "workflow": result.workflow,
                "manifest_digest": result.manifest_digest,
                "outputs": result.outputs,
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


def _print_error(status: str, category: str, path: str, message: str) -> None:
    """Write one structured command error to stderr."""

    print(
        json.dumps(
            {
                "status": status,
                "category": category,
                "path": path,
                "message": message,
            }
        ),
        file=sys.stderr,
    )


def main() -> None:
    """Run the CLI as a console script."""

    raise SystemExit(run())
