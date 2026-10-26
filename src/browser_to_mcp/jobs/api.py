"""One operation dispatcher shared by local CLI and MCP adapters."""

from pydantic import ValidationError

from .models import Budgets, JobError
from .store import JobStore


def dispatch(store: JobStore, operation: str, arguments: dict):
    try:
        if operation in {"start", "start-task"}:
            raw_limits = arguments.get("budgets")
            limits = Budgets.model_validate({} if raw_limits is None else raw_limits)
            if operation == "start":
                return store.start(arguments["request_key"], arguments["candidate"], limits)
            return store.start_task(
                arguments["request_key"],
                arguments["task"],
                limits,
                generation=arguments.get("generation", "client"),
            )
        if operation == "context":
            return store.read_context(arguments["job_id"])
        if operation == "submit-proposal":
            return store.submit_proposal(
                arguments["job_id"],
                arguments["question_id"],
                arguments["revision"],
                arguments["proposal"],
            )
        if operation == "read":
            return store.read(arguments["job_id"])
        if operation == "answer":
            return store.answer(
                arguments["job_id"],
                arguments["question_id"],
                arguments["revision"],
                arguments["value"],
            )
        if operation == "cancel":
            return store.cancel(arguments["job_id"])
        raise JobError("invalid_request")
    except (KeyError, TypeError, ValidationError):
        raise JobError("invalid_request") from None
