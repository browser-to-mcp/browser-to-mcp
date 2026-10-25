"""One operation dispatcher shared by local CLI and MCP adapters."""

from pydantic import ValidationError

from .models import Budgets, JobError
from .store import JobStore


def dispatch(store: JobStore, operation: str, arguments: dict):
    try:
        if operation == "start":
            limits = arguments.get("budgets")
            return store.start(
                arguments["request_key"],
                arguments["candidate"],
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
