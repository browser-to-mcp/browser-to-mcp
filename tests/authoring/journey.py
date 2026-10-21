"""Real capture/authoring and bounded STDIO helpers; no replay stubs."""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.authoring.browser import SelectionSession
from browser_to_mcp.authoring.command import author
from browser_to_mcp.recorder.draft import save_draft
from browser_to_mcp.recorder.session import RecordingSession
from browser_to_mcp.tracing import WorkflowTrace


async def create_workflow(catalog, tmp_path, *, approval="save", typed=False, via_link=False):
    draft_path = tmp_path / "book.recording.json"
    async with RecordingSession(
        catalog + "/catalogue/a-light-in-the-attic_1000/index.html", ["127.0.0.1"], headless=True
    ) as recording:
        if via_link:
            await recording.page.locator("#book-b").click()
            await recording.page.wait_for_url("**/shakespeares-sonnets_989/index.html")
        draft = await recording.finish()
        assert recording.browser is None and recording.context is None
    assert draft.status == "captured"
    if via_link:
        assert [event.kind for event in draft.events] == ["navigation", "click", "navigation"]
    save_draft(draft, draft_path, tmp_path / "workflows")
    sessions = []

    def factory(*args, **kwargs):
        session = SelectionSession(*args, headless=True, **kwargs)
        sessions.append(session)
        return session

    answers = ["yes"]
    if via_link:
        answers += ["yes", "yes", "yes"]  # click review, observed navigation, review
    answers += ["yes", "catalog_product_lookup", "Look up a book"]
    if via_link:
        answers += ["no"]
    else:
        answers += ["yes", "product_slug", "string", "yes", "Book slug", "1", "0/path:2", "no"]
    answers += ["no"]  # no fixed readiness wait
    fields = [
        (".product_main h1", "title", "string"),
        (".product_main .price_color", "price", "string"),
    ]
    if typed:
        fields += [
            ("#count", "count", "integer"),
            ("#available", "available", "boolean"),
            ("#amount", "amount", "number"),
        ]
    for index, (selector, name, kind) in enumerate(fields):
        answers += [
            "select:" + selector,
            name,
            kind,
            name,
            "yes" if index < len(fields) - 1 else "no",
        ]
    answers += [approval]
    answers = iter(answers)

    async def prompt(_message):
        answer = next(answers)
        if answer.startswith("select:"):
            await sessions[0].page.locator(answer.removeprefix("select:")).click()
            return ""
        return answer

    destination = tmp_path / "workflows/book.yaml"
    destination.parent.mkdir(exist_ok=True)
    try:
        await author(draft_path, destination, prompt=prompt, session_factory=factory)
    finally:
        assert all(session.browser is None for session in sessions)
    return destination


@asynccontextmanager
async def mcp_client(workflow_dir, trace_dir):
    pid_file = trace_dir.parent / (trace_dir.name + ".pid")
    # Only records process identity. The normal CLI and replay engine run unchanged.
    launcher = (
        "import os,sys; from pathlib import Path; "
        "Path(sys.argv.pop(1)).write_text(str(os.getpid())); "
        "from browser_to_mcp.cli import main; main()"
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            launcher,
            str(pid_file),
            "server",
            "--workflow-dir",
            str(workflow_dir),
            "--trace-dir",
            str(trace_dir),
            "--timeout-seconds",
            "60",
        ],
        env={key: os.environ[key] for key in ["PLAYWRIGHT_BROWSERS_PATH"] if key in os.environ},
    )
    try:
        async with asyncio.timeout(180):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    yield client
    finally:
        if pid_file.exists():
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)


def inspect_trace(path, loaded, trace_dir, *, outputs=None, category=None, error_path=None):
    path = Path(path)
    assert path.parent.resolve() == trace_dir.resolve()
    trace = json.loads(path.read_text())
    WorkflowTrace.model_validate(trace)
    assert trace["metadata"]["workflow_name"] == loaded.workflow.name
    assert trace["metadata"]["workflow_version"] == loaded.workflow.version
    assert trace["metadata"]["manifest_digest"] == loaded.manifest_digest
    assert trace["metadata"]["run_id"] == path.stem
    if category is None:
        assert trace["status"] == "completed"
        assert trace["outputs"] == outputs
        for key, value in outputs.items():
            assert type(trace["outputs"][key]) is type(value)
        assert trace["error"] is None
        assert all(action["status"] == "completed" for action in trace["actions"])
        assert all(check["status"] == "passed" for check in trace["assertions"])
    else:
        assert trace["status"] == "failed"
        assert trace["error"]["category"] == category
        assert trace["error"]["path"] == error_path
        assert trace["error"]["message"]
    return trace
