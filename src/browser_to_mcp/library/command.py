"""Thin local CLI over the shared version library."""

import json
import sqlite3
import sys
from pathlib import Path

from browser_to_mcp.compiler import SchemaCompilationError
from browser_to_mcp.jobs.models import JobError
from browser_to_mcp.server import ServerConfigurationError, create_server

from .store import Library, LibraryError


def add_library_parser(subparsers):
    parser = subparsers.add_parser(
        "library", help="Review, publish and roll back tested tool versions."
    )
    parser.add_argument("--directory", type=Path, default=Path(".browser-to-mcp-library"))
    commands = parser.add_subparsers(dest="library_command", required=True)
    imp = commands.add_parser(
        "import", help="Validate YAML and import an immutable draft, never publish."
    )
    imp.add_argument("candidate", type=Path)
    imp.add_argument("--origin", required=True)
    imp.add_argument("--tool-id", help="Append a version to this existing stable tool ID.")
    imp.add_argument("--display-name")
    commands.add_parser("list")
    for name in ("review", "test", "publish", "rollback"):
        cmd = commands.add_parser(name)
        cmd.add_argument("tool_id")
        cmd.add_argument("version", type=int)
        if name == "test":
            cmd.add_argument("--evidence-dir", type=Path, required=True)
            cmd.add_argument("--suite-sha256", required=True)
        if name in ("publish", "rollback"):
            cmd.add_argument("--revision", type=int, required=True)
            cmd.add_argument("--candidate-sha256", required=True)
        if name == "publish":
            cmd.add_argument(
                "--approve",
                action="store_true",
                required=True,
                help="Explicitly approve the complete reviewed plan and test evidence.",
            )
    for name in ("history", "retire"):
        cmd = commands.add_parser(name)
        cmd.add_argument("tool_id")
        if name == "retire":
            cmd.add_argument("--revision", type=int, required=True)
    server = commands.add_parser(
        "server", help="Serve only approved published versions; restart to reload."
    )
    server.add_argument("--trace-dir", type=Path, default=Path("traces"))
    server.add_argument("--timeout-seconds", type=int, default=300)
    server.add_argument(
        "--catalog",
        action="append",
        default=[],
        metavar="CATALOG=WORKFLOW",
        help="Add books, laptops or clothing to collect_catalog using a published workflow name.",
    )


def run_library(args):
    try:
        library = Library(args.directory)
        op = args.library_command
        if op == "server":
            create_server(
                library.published(), args.trace_dir, args.timeout_seconds, catalogs=args.catalog
            ).run(transport="stdio")
            return 0
        if op == "import":
            result = library.import_draft(
                args.candidate,
                origin=args.origin,
                tool_id=args.tool_id,
                display_name=args.display_name,
            )
        elif op == "list":
            result = library.list()
        elif op == "history":
            result = library.history(args.tool_id)
        elif op == "review":
            result = library.review(args.tool_id, args.version)
        elif op == "test":
            result = library.attach_tests(
                args.tool_id, args.version, args.evidence_dir, args.suite_sha256
            )
        elif op == "retire":
            result = library.retire(args.tool_id, revision=args.revision)
        else:
            result = library.activate(
                args.tool_id,
                args.version,
                revision=args.revision,
                candidate_sha256=args.candidate_sha256,
                approve=op == "publish" and args.approve,
                rollback=op == "rollback",
            )
        print(json.dumps(result, sort_keys=True))
        return (
            1
            if isinstance(result, dict) and result.get("version", {}).get("state") == "failed"
            else 0
        )
    except (LibraryError, ServerConfigurationError) as error:
        print(json.dumps({"status": "error", "message": str(error)}), file=sys.stderr)
        return 2
    except (
        ValueError,
        OSError,
        sqlite3.Error,
        KeyError,
        RecursionError,
        JobError,
        SchemaCompilationError,
    ):
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "Library operation refused. Check candidate, "
                    "evidence, plan hash and destination; no partial publication was made.",
                }
            ),
            file=sys.stderr,
        )
        return 2
