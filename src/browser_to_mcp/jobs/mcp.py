"""Job management over local STDIO; reconnecting never owns or restarts a worker."""

import json

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .api import dispatch
from .models import JobError
from .store import JobStore


def create_job_server(directory):
    store = JobStore(directory)
    server = FastMCP(
        name="browser-to-mcp-build-jobs",
        instructions="Manage local draft-preparation jobs. Run the separate jobs worker first. "
        "Status reads never start work. Supply public workflow data only, never login secrets. "
        "Keeping a draft completes its job but does not run or publish a tool.",
    )

    def invoke(operation, arguments):
        try:
            result = dispatch(store, operation, arguments)
            failed = False
        except JobError as error:
            result, failed = error.payload(), True
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result))],
            structuredContent=result,
            isError=failed,
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )
    def start_build_job(
        request_key: str, candidate: str, budgets: dict[str, object] | None = None
    ) -> CallToolResult:
        """Queue a public YAML candidate. Reuse the UUID request key only for the same attempt."""
        return invoke("start", dict(request_key=request_key, candidate=candidate, budgets=budgets))

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )
    def start_task_build_job(
        request_key: str, task: dict[str, object], budgets: dict[str, object] | None = None
    ) -> CallToolResult:
        """Queue a public URL/task description for a draft. Explicitly grant resource budgets.

        The task declares name, start_url, description, allowed_domains, inputs, and fields.
        Read status and answer any shape/review question. Configure the local model on the worker.
        Never supply credentials. This operation cannot publish or register tools.
        """
        return invoke("start-task", dict(request_key=request_key, task=task, budgets=budgets))

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )
    def read_build_job(job_id: str) -> CallToolResult:
        """Read the saved status and current question without starting or repeating any work."""
        return invoke("read", dict(job_id=job_id))

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
        )
    )
    def answer_build_job(
        job_id: str, question_id: str, revision: int, value: str
    ) -> CallToolResult:
        """Answer the exact current question with a listed choice. Never send credentials."""
        return invoke(
            "answer", dict(job_id=job_id, question_id=question_id, revision=revision, value=value)
        )

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )
    def cancel_build_job(job_id: str) -> CallToolResult:
        """Request cancellation; poll until work stops. Terminal jobs remain unchanged."""
        return invoke("cancel", dict(job_id=job_id))

    return server
