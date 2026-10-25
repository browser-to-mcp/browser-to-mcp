"""Thin JSON CLI for the shared job service."""

import json
import sys
from pathlib import Path

from .api import dispatch
from .candidate import MAX_SOURCE_BYTES
from .models import Budgets, JobError
from .store import JobStore

_FILE_INPUTS = {
    "start": ("candidate_path", "candidate", "invalid_candidate"),
    "start-task": ("task_path", "task", "invalid_task"),
    "submit-proposal": ("proposal_path", "proposal", "invalid_proposal"),
}


def add_jobs_parser(subparsers):
    parser = subparsers.add_parser("jobs", help="Track local workflow draft-preparation jobs.")
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path(".browser-to-mcp-jobs"),
        help="Private local job directory shared by worker, CLI, and MCP clients.",
    )
    commands = parser.add_subparsers(dest="jobs_command", required=True)
    start = commands.add_parser("start", help="Queue an existing public YAML candidate for review.")
    start.add_argument("candidate_path", type=Path)
    task = commands.add_parser(
        "start-task", help="Build a draft from a public URL and a JSON task description."
    )
    task.add_argument("task_path", type=Path)
    task.add_argument(
        "--generation",
        choices=["client", "provider"],
        default="client",
        help="Client supplies the proposal (default), or a configured provider creates it.",
    )
    for command in (start, task):
        command.add_argument("--request-key", required=True, help="UUID v4 for this attempt.")
        for name, field in Budgets.model_fields.items():
            command.add_argument("--" + name.replace("_", "-"), type=int, default=field.default)
    read = commands.add_parser("read", help="Read status without starting or changing work.")
    read.add_argument("job_id")
    context = commands.add_parser("context", help="Read a waiting task's page view and schema.")
    context.add_argument("job_id")
    proposal = commands.add_parser("submit-proposal", help="Submit a client's JSON proposal.")
    proposal.add_argument("job_id")
    proposal.add_argument("proposal_path", type=Path)
    proposal.add_argument("--question-id", required=True)
    proposal.add_argument("--revision", type=int, required=True)
    answer = commands.add_parser("answer", help="Answer the current question with a listed choice.")
    answer.add_argument("job_id")
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--revision", type=int, required=True)
    answer.add_argument("--value", required=True)
    cancel = commands.add_parser(
        "cancel", help="Cancel queued work or request running work to stop."
    )
    cancel.add_argument("job_id")
    worker = commands.add_parser("worker", help="Run the single local owner until Ctrl+C.")
    worker.add_argument(
        "--once", action="store_true", help="Process at most one queued phase and exit."
    )
    commands.add_parser("cleanup", help="Remove expired records and discarded artifacts.")
    commands.add_parser("server", help="Expose job operations over a local MCP STDIO connection.")


def run_jobs(namespace):
    store = JobStore(namespace.directory)
    operation = namespace.jobs_command
    try:
        if operation == "worker":
            # Process supervision uses POSIX signals and process groups.
            from .worker import run_worker

            run_worker(namespace.directory, once=namespace.once)
            return 0
        if operation == "server":
            from .mcp import create_job_server

            create_job_server(namespace.directory).run(transport="stdio")
            return 0
        if operation == "cleanup":
            store.maintain()
            result = {"status": "cleaned"}
        else:
            arguments = vars(namespace).copy()
            if operation in _FILE_INPUTS:
                path_argument, field, error_code = _FILE_INPUTS[operation]
                path = getattr(namespace, path_argument)
                try:
                    if not path.is_file():
                        raise OSError
                    with path.open("rb") as stream:
                        source = stream.read(MAX_SOURCE_BYTES + 1).decode("utf-8")
                    if len(source.encode()) > MAX_SOURCE_BYTES:
                        raise ValueError
                    arguments[field] = source if operation == "start" else json.loads(source)
                except (OSError, ValueError, RecursionError):
                    raise JobError(error_code) from None
                if operation != "submit-proposal":
                    arguments["budgets"] = {name: arguments[name] for name in Budgets.model_fields}
            result = dispatch(store, operation, arguments)
        print(json.dumps(result, sort_keys=True))
        return 0
    except JobError as error:
        print(json.dumps(error.payload(), sort_keys=True), file=sys.stderr)
        return 2
