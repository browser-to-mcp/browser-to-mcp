"""One operation dispatcher shared by local CLI and MCP adapters."""

from pydantic import ValidationError

from .models import Budgets, JobError
from .store import JobStore


def dispatch(store: JobStore, operation: str, arguments: dict):
    try:
        if operation in {"start", "start-task"}:
            limits = arguments.get("budgets")
            start = store.start if operation == "start" else store.start_task
            return start(
                arguments["request_key"],
                arguments["candidate" if operation == "start" else "task"],
                Budgets.model_validate({} if limits is None else limits),
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
