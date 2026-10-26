"""Client-led MCP creation uses no worker-side model or API credentials."""

import asyncio
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from builder_site import expected, proposal, task
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.jobs.candidate import check_candidate
from browser_to_mcp.jobs.models import Budgets, JobError
from browser_to_mcp.jobs.worker import execute_phase, work
from browser_to_mcp.replay import replay_workflow


def phase(store):
    job = store.claim()
    if job:
        asyncio.run(execute_phase(store, job["id"]))


def inspect(store, request, **limits):
    job = store.start_task(str(uuid4()), request, Budgets(browser_actions=6, **limits))
    phase(store)
    assert store.read(job["id"])["question"]["kind"] == "draft_proposal"
    return store.read_context(job["id"])


def submit(store, context, value):
    return store.submit_proposal(
        context["job_id"],
        context["question_id"],
        context["revision"],
        value,
    )


@pytest.mark.parametrize("layout", ["books", "laptops", "clothing"])
def test_client_proposal_previews_and_replays_without_a_model_install(
    store,
    catalog_site,
    layout,
    monkeypatch,
):
    site, base = catalog_site
    # An unusable provider configuration cannot affect default client generation.
    monkeypatch.setenv("BROWSER_TO_MCP_PROVIDER", "not-a-provider")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE-KEY")
    context = inspect(store, task(base, layout))
    before = store.read(context["job_id"])
    assert before["generation"] == "client"
    assert before["usage"]["browser_actions"] == 3
    assert before["question"]["choices"] == []
    assert context["context"]["task"]["shape"] == "array"
    assert "untrusted_page" in context["context"]
    assert "NEVER-SEND" not in json.dumps(context)
    for _ in range(3):
        assert store.read_context(context["job_id"]) == context
        assert store.read(context["job_id"]) == before
    assert len(site.hits) == 1
    # Restarting an idle worker preserves the question without re-inspecting the website.
    asyncio.run(work(store, asyncio.Event(), once=True))
    assert store.read_context(context["job_id"]) == context
    assert len(site.hits) == 1
    assert submit(store, context, proposal(layout))["state"] == "queued"
    phase(store)
    ready = store.read(context["job_id"])
    assert ready["state"] == "ready_for_review", ready
    assert ready["usage"]["browser_actions"] == 6
    assert ready["usage"]["model_requests"] == ready["usage"]["tokens"] == 0
    report = ready["report"]
    assert report["provider"] == "mcp_client" and report["cost_basis"] == "external_client"
    for key in [
        "prompt_tokens",
        "completion_tokens",
        "model_seconds",
        "api_cost_microusd",
        "estimated_cost_microusd",
    ]:
        assert report[key] is None
    assert "PRIVATE" not in json.dumps(ready)
    assert "untrusted_page" not in json.dumps(ready)
    loaded, _ = check_candidate(store.read_artifact(ready["id"], "draft").decode())
    result = asyncio.run(replay_workflow(loaded, ["query=notebook"]))
    assert result.outputs["result"] == expected(base)
    assert result.outputs["collection"] == dict(pages_read=2, complete=True, stop_reason="end")
    store.maintain()
    assert not list(store.directory.glob("*.context.candidate"))
    assert not list(store.directory.glob("*.proposal.candidate"))
    assert not list(store.directory.glob("*.yaml"))


def test_shape_answer_is_saved_across_the_proposal_question(store, catalog_site):
    site, base = catalog_site
    request = task(base, shape=None, max_pages=1, allow_empty=False)
    job = store.start_task(str(uuid4()), request, Budgets(browser_actions=6))
    phase(store)
    waiting = store.read(job["id"])
    assert site.hits == [] and waiting["question"]["kind"] == "output_shape"
    store.answer(job["id"], waiting["question"]["id"], waiting["revision"], "list")
    phase(store)
    context = store.read_context(job["id"])
    assert context["context"]["task"]["shape"] == "array"
    submit(store, context, proposal(paged=False, empty=False))
    phase(store)
    assert store.read(job["id"])["state"] == "ready_for_review"


@pytest.mark.parametrize(
    "change",
    [
        {"permissions": {"allowed_domains": ["evil.example"]}},
        {"javascript": "fetch('https://evil.example')"},
        {"steps": [{"action": "click", "selector": ".buy"}]},
        {"bindings": {}},
    ],
)
def test_bad_client_proposals_cannot_change_permissions_or_consume_a_question(
    store,
    catalog_site,
    change,
):
    _, base = catalog_site
    context = inspect(store, task(base))
    before = store.read(context["job_id"])
    with pytest.raises(JobError):
        submit(store, context, proposal() | change)
    assert store.read(context["job_id"]) == before
    assert not list(store.directory.glob("*.proposal.candidate"))
    with pytest.raises(JobError) as raised:
        store.answer(context["job_id"], context["question_id"], context["revision"], "keep")
    assert raised.value.code == "invalid_answer"


def test_only_one_concurrent_submission_can_advance_the_job(store, catalog_site):
    _, base = catalog_site
    context = inspect(store, task(base))

    def attempt(_):
        try:
            return submit(store, context, proposal())["state"]
        except JobError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, range(2)))
    assert outcomes.count("queued") == 1
    assert all(value in {"queued", "stale_answer", "invalid_transition"} for value in outcomes)
    with pytest.raises(JobError):
        submit(store, context, proposal())
    assert len(list(store.directory.glob("*.proposal.candidate"))) == 1


@pytest.mark.parametrize("point", ["waiting", "queued"])
def test_cancellation_prevents_later_preview_and_removes_context(store, catalog_site, point):
    site, base = catalog_site
    context = inspect(store, task(base))
    if point == "queued":
        submit(store, context, proposal())
    store.cancel(context["job_id"])
    phase(store)
    with pytest.raises(JobError):
        store.read_context(context["job_id"])
    with pytest.raises(JobError):
        submit(store, context, proposal())
    store.maintain()
    assert store.read(context["job_id"])["state"] == "cancelled"
    assert not list(store.directory.glob("*.candidate"))
    assert len(site.hits) == 1


@pytest.mark.parametrize("kind", ["context", "proposal"])
def test_tampered_client_artifacts_cannot_reach_review(store, catalog_site, kind):
    _, base = catalog_site
    context = inspect(store, task(base))
    if kind == "proposal":
        submit(store, context, proposal())
    store.artifact_path(context["job_id"], kind).write_text('{"permissions":"changed"}')
    if kind == "context":
        with pytest.raises(JobError):
            store.read_context(context["job_id"])
    else:
        phase(store)
        assert store.read(context["job_id"])["state"] == "failed"
        assert store.read(context["job_id"])["artifact"] is None


def test_waiting_deadline_cannot_be_extended_by_reading_or_submitting(store, catalog_site):
    _, base = catalog_site
    context = inspect(store, task(base))
    before = store.read(context["job_id"])
    store.clock = lambda: before["deadline"] + 1
    with pytest.raises(JobError, match="total time"):
        store.read_context(context["job_id"])
    assert store.read(context["job_id"]) == before
    with pytest.raises(JobError):
        submit(store, context, proposal())
    store.maintain()
    assert store.read(context["job_id"])["state"] == "failed"


def test_client_unsupported_answer_fails_without_another_website_visit(store, catalog_site):
    site, base = catalog_site
    context = inspect(store, task(base))
    result = submit(store, context, {"status": "unsupported", "reason": "challenge"})
    assert result["state"] == "failed" and result["error"]["code"] == "task_challenge"
    assert len(site.hits) == 1


def test_client_bad_selectors_fail_preview_without_publication(store, catalog_site):
    _, base = catalog_site
    context = inspect(store, task(base, timeout_seconds=1))
    submit(store, context, proposal() | {"area": "#absent"})
    phase(store)
    result = store.read(context["job_id"])
    assert result["state"] == "failed" and result["error"]["code"] == "preview_failed"
    assert result["artifact"] is None


def test_real_mcp_reconnects_complete_a_client_build_without_worker_credentials(
    store,
    catalog_site,
    monkeypatch,
):
    _, base = catalog_site
    monkeypatch.delenv("BROWSER_TO_MCP_PROVIDER", raising=False)
    monkeypatch.delenv("BROWSER_TO_MCP_MODEL_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "from browser_to_mcp.cli import main; main()",
            "jobs",
            "--directory",
            str(store.directory),
            "server",
        ],
        env={name: os.environ[name] for name in ["PLAYWRIGHT_BROWSERS_PATH"] if name in os.environ},
    )

    async def call(name, arguments):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                result = await client.call_tool(name, arguments)
                assert not result.isError, result
                return result.structuredContent

    def once():
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from browser_to_mcp.cli import main; main()",
                "jobs",
                "--directory",
                str(store.directory),
                "worker",
                "--once",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr

    initial = asyncio.run(
        call(
            "start_task_build_job",
            dict(
                request_key=str(uuid4()),
                task=task(base),
                budgets={"browser_actions": 6},
            ),
        )
    )
    assert initial["generation"] == "client"
    once()
    context = asyncio.run(call("read_task_context", {"job_id": initial["id"]}))
    asyncio.run(
        call(
            "submit_task_proposal",
            dict(
                job_id=initial["id"],
                question_id=context["question_id"],
                revision=context["revision"],
                proposal=proposal(),
            ),
        )
    )
    once()
    ready = asyncio.run(call("read_build_job", {"job_id": initial["id"]}))
    assert ready["state"] == "ready_for_review"
    asyncio.run(
        call(
            "answer_build_job",
            dict(
                job_id=initial["id"],
                question_id=ready["question"]["id"],
                revision=ready["revision"],
                value="keep",
            ),
        )
    )
    once()
    assert store.read(initial["id"])["state"] == "completed"


def test_accepted_proposal_bytes_cannot_exceed_the_job_output_budget(store, catalog_site):
    _, base = catalog_site
    first = inspect(store, task(base))
    used = store.read(first["job_id"])["usage"]["output_bytes"]
    store.cancel(first["job_id"])
    context = inspect(store, task(base), output_bytes=used + 100)
    with pytest.raises(JobError) as raised:
        submit(store, context, proposal())
    assert raised.value.code == "budget_output_bytes"
    current = store.read(context["job_id"])
    assert current["state"] == "failed" and current["artifact"] is None
    assert not list(store.directory.glob("*.proposal.candidate"))


def test_generation_choice_is_part_of_the_request_identity(store, catalog_site):
    _, base = catalog_site
    key = str(uuid4())
    initial = store.start_task(key, task(base), Budgets())
    assert store.start_task(key, task(base), Budgets()) == initial
    with pytest.raises(JobError) as raised:
        store.start_task(key, task(base), Budgets(), generation="provider")
    assert raised.value.code == "conflict"
    for choice in ["unknown", [], None, True]:
        with pytest.raises(JobError):
            store.start_task(str(uuid4()), task(base), Budgets(), generation=choice)


def test_page_access_failure_during_client_check_has_a_clear_error(store, catalog_site):
    site, base = catalog_site
    context = inspect(store, task(base))
    submit(store, context, proposal())
    site.mode = "blocked"
    phase(store)
    result = store.read(context["job_id"])
    assert result["state"] == "failed"
    assert result["error"]["code"] == "page_unavailable"
    assert result["artifact"] is None
