"""Terminal adapters for independent candidate checks and evidence verification."""

import asyncio
import json
import sys
from pathlib import Path

from browser_to_mcp.compiler import SchemaCompilationError
from browser_to_mcp.jobs.models import BuildReport, JobError

from .service import evaluate, read_json, verify_evaluation


def add_evaluation_parsers(subparsers):
    for name, help_text in (
        ("evaluate", "Test a draft against separately prepared expected answers."),
        ("check-evaluation", "Verify saved draft evidence without browsing or publishing."),
    ):
        parser = subparsers.add_parser(name, help=help_text)
        parser.add_argument("candidate", type=Path)
        parser.add_argument(
            "suite", type=Path, help="Independently reviewed public JSON test plan."
        )
        parser.add_argument("--suite-sha256", required=True, help="Approved sha256:HEX plan hash.")
        parser.add_argument(
            "--evidence-dir",
            type=Path,
            required=True,
            help="New attempt directory for evaluate; existing attempt for check-evaluation.",
        )
        if name == "evaluate":
            parser.add_argument(
                "--generation-report",
                type=Path,
                help="Optional saved BuildReport JSON. Omitted model usage stays unknown.",
            )


def run_evaluation(namespace):
    try:
        if namespace.command == "evaluate":
            generation = (
                BuildReport.model_validate(read_json(namespace.generation_report))
                if namespace.generation_report
                else None
            )
            report = asyncio.run(
                evaluate(
                    namespace.candidate,
                    namespace.suite,
                    namespace.evidence_dir,
                    namespace.suite_sha256,
                    generation=generation,
                )
            )
        else:
            report = verify_evaluation(
                namespace.candidate, namespace.suite, namespace.evidence_dir, namespace.suite_sha256
            )
        print(report.model_dump_json())
        return 0 if report.eligible else 1
    except (OSError, ValueError, JobError, TimeoutError, SchemaCompilationError, RecursionError):
        # Parsers may quote page values. Details belong in the private local evidence.
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "The candidate, test plan or evidence could not be verified. "
                    "Check paths, hashes and report.json. Use a new directory for each attempt.",
                }
            ),
            file=sys.stderr,
        )
        return 2
