"""Command-line entry point for browser-to-mcp."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from browser_to_mcp.loader import load_workflow
from browser_to_mcp.validation import WorkflowValidationError


def build_parser() -> argparse.ArgumentParser:
    """Build the M0 CLI parser."""

    parser = argparse.ArgumentParser(prog="browser-to-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="Validate a workflow YAML contract.")
    validate_parser.add_argument("workflow_path", type=Path)
    return parser


def run(arguments: list[str] | None = None) -> int:
    """Run a CLI command and return a process exit code."""

    parser = build_parser()
    namespace = parser.parse_args(arguments)

    if namespace.command == "validate":
        return _validate(namespace.workflow_path)
    parser.error(f"Unsupported command: {namespace.command}")
    return 2


def _validate(path: Path) -> int:
    try:
        loaded = load_workflow(path)
    except WorkflowValidationError as error:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "category": error.category,
                    "path": error.path,
                    "message": error.message,
                }
            ),
            file=sys.stderr,
        )
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


def main() -> None:
    """Run the CLI as a console script."""

    raise SystemExit(run())
