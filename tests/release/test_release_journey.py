"""Build nine times, then verify 90 real calls against independent fixed answers."""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest
from builder_site import expected, proposal, serve, task
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.evaluation.service import _exact
from browser_to_mcp.interface.service import Interface
from browser_to_mcp.library.store import LibraryError
from browser_to_mcp.tracing import WorkflowTrace


@asynccontextmanager
async def client(*arguments):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", "from browser_to_mcp.cli import main; main()", *map(str, arguments)],
        env={k: os.environ[k] for k in ("PLAYWRIGHT_BROWSERS_PATH",) if k in os.environ},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def until(read, states):
    async with asyncio.timeout(120):
        while True:
            result = read()
            if result["state"] in states:
                return result
            await asyncio.sleep(0.05)


def outputs(base, case):
    return {
        "result": expected(
            base, () if case == "empty" else (0, 1) if case == "limited" else (0, 1, 2)
        ),
        "collection": {
            "pages_read": 2 if case == "full" else 1,
            "complete": case != "limited",
            "stop_reason": "page_limit" if case == "limited" else "end",
        },
    }


def plan(base):
    cases = []
    for name, inputs, tags, answer in (
        (
            "full",
            {"query": "notebook", "max_pages": 20},
            ["demonstration", "multiple_pages"],
            "full",
        ),
        ("limited", {"query": "notebook", "max_pages": 1}, ["changed_inputs"], "limited"),
        ("empty", {"query": "empty", "max_pages": 20}, ["changed_inputs", "zero_results"], "empty"),
        ("combined", {"query": "empty", "max_pages": 1}, ["combined_filters"], "empty"),
    ):
        cases.append(
            dict(
                id=name,
                inputs=inputs,
                covers=tags,
                expected={"outcome": "outputs", "outputs": outputs(base, answer)},
            )
        )
    cases += [
        dict(
            id="invalid",
            inputs={},
            covers=["invalid_input"],
            expected={"outcome": "error", "category": "input", "path": "inputs"},
        ),
        dict(
            id="changed_page",
            inputs={"query": "broken"},
            covers=["changed_page"],
            expected={
                "outcome": "error",
                "category": "assertion",
                "path": "outputs.result.0.price_text",
            },
        ),
    ]
    return dict(
        name="Three-site fixed answers",
        mode="controlled",
        timeout_seconds=5,
        requirements_source="docs/THREE_SITE_PILOT.md",
        expectations_source="Independent blue/green/red notebook answers",
        cases=cases,
    )


async def call(session, name, **arguments):
    response = await session.call_tool(name, arguments)
    assert not response.isError, response
    return response.structuredContent


async def build(service, session, base, layout, attempt, report):
    observation = dict(layout=layout, attempt=attempt, matched=False, seconds=0)
    report["builds"].append(observation)  # Keep every started attempt, including failures.
    started = monotonic()
    try:
        request = task(base, layout, max_pages=20, max_pages_input="max_pages", timeout_seconds=2)
        request["name"] = "collect_" + layout
        request["max_items"] = 10 + attempt
        job = await call(
            session,
            "start_task_build_job",
            request_key=str(uuid4()),
            task=request,
            budgets={"browser_actions": 6},
        )
        key = job["id"]
        observation["job_id"] = key
        job = await until(lambda: service.jobs.read(key), {"waiting_for_input", "failed"})
        assert job["question"]["kind"] == "draft_proposal", job
        context = await call(session, "read_task_context", job_id=key)
        before = service.status()["jobs"]
        assert (await call(session, "read_task_context", job_id=key)) == context
        assert service.status()["jobs"] == before
        await call(
            session,
            "submit_task_proposal",
            job_id=key,
            question_id=job["question"]["id"],
            revision=job["revision"],
            proposal=proposal(layout),
        )
        job = await until(lambda: service.jobs.read(key), {"ready_for_review", "failed"})
        assert job["state"] == "ready_for_review", job
        assert job["usage"]["model_requests"] == 0
        assert job["report"]["api_cost_microusd"] is None
        observation["generation"] = job["report"]
        prior = service.library.list()
        await call(
            session,
            "answer_build_job",
            job_id=key,
            question_id=job["question"]["id"],
            revision=job["revision"],
            value="keep",
        )
        await until(lambda: service.jobs.read(key), {"completed", "failed"})
        review = service.dispatch(
            "import", {"job_id": key, **({"tool_id": prior[0]["id"]} if prior else {})}
        )
        tool, number = review["tool"]["id"], review["version"]["version"]
        publish = dict(
            tool_id=tool,
            version=number,
            revision=review["tool"]["revision"],
            candidate_sha256=review["version"]["sha"],
            approve=True,
        )
        with pytest.raises(LibraryError, match="passing evidence"):
            service.dispatch("publish", publish)
        op = service.dispatch(
            "evaluate",
            dict(
                tool_id=tool,
                version=number,
                suite=plan(base),
                approved_plan=True,
                request_key=str(uuid4()),
            ),
        )
        checked = await until(
            lambda: service.operation(op["operation_id"]), {"completed", "failed"}
        )
        assert checked["state"] == "completed", checked
        assert checked["result"]["report"]["eligible"], checked
        review = service.library.review(tool, number)
        publish["revision"] = review["tool"]["revision"]
        service.dispatch("publish", publish)
        async with client(
            "library",
            "--directory",
            service.library.directory,
            "server",
            "--trace-dir",
            service.directory / "traces",
        ) as tools:
            listed = (await tools.list_tools()).tools
            assert [t.name for t in listed] == [request["name"]]
            limit = listed[0].inputSchema["properties"]["max_pages"]
            assert (limit["minimum"], limit["maximum"], limit["default"]) == (1, 20, 20)
            result = await tools.call_tool(request["name"], {"query": "empty", "max_pages": 1})
            assert not result.isError, result
            assert _exact(result.structuredContent["outputs"], outputs(base, "empty"))
            assert result.meta["publication"]["version"] == number
        observation.update(
            matched=True,
            candidate_sha256=review["version"]["sha"],
            manifest_digest=review["version"]["manifest"],
            tool_id=tool,
            version=number,
            evaluation_operation=op["operation_id"],
        )
        return review
    finally:
        observation["seconds"] = round(monotonic() - started, 3)


@pytest.mark.parametrize("layout", ["books", "laptops", "clothing"])
def test_complete_three_site_journey(layout, release_report):
    report, report_path = release_report
    report["mcp_sdk_version"] = version("mcp")
    root = report_path.parent / report_path.stem / layout
    journey = dict(layout=layout, matched=False)
    report["journeys"].append(journey)
    with serve() as (site, base), serve() as (outside, outside_base):
        site.outside_url = outside_base.replace("127.0.0.1", "localhost")
        service = Interface(root / "ui", root / "jobs", root / "library")

        async def exercise():
            async with client("jobs", "--directory", service.jobs.directory, "server") as session:
                for attempt in range(1, 4):
                    review = await build(service, session, base, layout, attempt, report)
            assert (
                len({b["candidate_sha256"] for b in report["builds"] if b["layout"] == layout}) == 3
            )
            tool = review["tool"]["id"]
            async with client(
                "library",
                "--directory",
                service.library.directory,
                "server",
                "--trace-dir",
                root / "traces",
            ) as session:
                for case, inputs in (
                    ("full", {"query": "notebook"}),
                    ("limited", {"query": "notebook", "max_pages": 1}),
                    ("empty", {"query": "empty"}),
                ):
                    for attempt in range(1, 11):
                        observation = dict(
                            layout=layout,
                            case=case,
                            attempt=attempt,
                            inputs=inputs,
                            matched=False,
                            seconds=0,
                        )
                        report["runs"].append(observation)
                        started = monotonic()
                        try:
                            response = await session.call_tool("collect_" + layout, inputs)
                            assert not response.isError, response
                            data = response.structuredContent
                            assert _exact(data["outputs"], outputs(base, case))
                            trace = WorkflowTrace.model_validate_json(
                                Path(data["trace_path"]).read_bytes()
                            )
                            assert _exact(trace.outputs, data["outputs"])
                            assert trace.metadata.manifest_digest == review["version"]["manifest"]
                            assert trace.metadata.publication["version"] == 3
                            assert response.meta["publication"]["tool_id"] == tool
                            assert trace.actions[-1].pages[-1].outcome == (
                                "page_limit" if case == "limited" else "end"
                            )
                            observation.update(
                                matched=True,
                                trace_path=data["trace_path"],
                                run_id=trace.metadata.run_id,
                                browser_version=trace.metadata.browser_version,
                                browser_seconds=data["duration_seconds"],
                            )
                        finally:
                            observation["seconds"] = round(monotonic() - started, 3)
                # Invalid protocol inputs must start no browser and produce no requests/traces.
                for inputs in (
                    {},
                    {"query": "notebook", "extra": 1},
                    *(
                        {"query": "notebook", "max_pages": value}
                        for value in (0, 21, True, 1.5, "2", None)
                    ),
                ):
                    before = list(site.hits)
                    traces = set((root / "traces").glob("*.json"))
                    response = await session.call_tool("collect_" + layout, inputs)
                    assert response.isError
                    assert site.hits == before and set((root / "traces").glob("*.json")) == traces
                    report["refusals"].append(dict(layout=layout, inputs=inputs, matched=True))
                for mode in (
                    "broken",
                    "conflict",
                    "loop",
                    "outside",
                    "missing_page",
                    "challenge",
                    "slow",
                    "blank_price",
                    "cart",
                    "unmarked_empty",
                ):
                    site.mode = mode
                    response = await session.call_tool("collect_" + layout, {"query": "notebook"})
                    assert response.isError, (mode, response)
                    data = response.structuredContent
                    trace = WorkflowTrace.model_validate_json(Path(data["trace_path"]).read_bytes())
                    assert trace.status == "failed" and trace.outputs == {}
                    report["refusals"].append(
                        dict(
                            layout=layout,
                            mode=mode,
                            matched=True,
                            error=data,
                            trace_path=data["trace_path"],
                        )
                    )
                assert outside.hits == []
                site.mode = "same_title"
                response = await session.call_tool("collect_" + layout, {"query": "notebook"})
                assert not response.isError, response
                assert len(response.structuredContent["outputs"]["result"]) == 3
                assert all(
                    row["title"] == "Same title"
                    for row in response.structuredContent["outputs"]["result"]
                )
                # Cancel an actual published run while the second page waits for content.
                site.mode = "slow"
                site.hits.clear()
                traces = set((service.directory / "traces").glob("*.json"))
                operation = service.dispatch(
                    "run",
                    dict(tool_id=tool, inputs={"query": "notebook"}, request_key=str(uuid4())),
                )
                async with asyncio.timeout(10):
                    while not any("page=2" in hit for hit in site.hits):
                        await asyncio.sleep(0.02)
                service.dispatch("cancel-operation", operation)
                async with asyncio.timeout(10):
                    while not (set((service.directory / "traces").glob("*.json")) - traces):
                        await asyncio.sleep(0.02)
                cancelled = service.operation(operation["operation_id"])
                assert cancelled["state"] == "cancelled" and cancelled["result"] is None
                new_trace = (set((service.directory / "traces").glob("*.json")) - traces).pop()
                trace = WorkflowTrace.model_validate_json(new_trace.read_bytes())
                assert trace.status == "failed" and trace.outputs == {}
                assert trace.actions[-1].pages[-1].outcome == "cancelled"
                report["refusals"].append(
                    dict(
                        layout=layout, mode="cancelled_run", matched=True, trace_path=str(new_trace)
                    )
                )
                site.mode = "normal"
                # Publication changes affect only a newly connected server.
                service.library.activate(
                    tool,
                    1,
                    revision=service.library.review(tool, 1)["tool"]["revision"],
                    candidate_sha256=service.library.review(tool, 1)["version"]["sha"],
                    rollback=True,
                )
                old = await session.call_tool("collect_" + layout, {"query": "empty"})
                assert old.meta["publication"]["version"] == 3
            async with client(
                "library", "--directory", service.library.directory, "server"
            ) as session:
                rolled_back = await session.call_tool("collect_" + layout, {"query": "empty"})
                assert not rolled_back.isError and rolled_back.meta["publication"]["version"] == 1
            service.library.retire(
                tool, revision=service.library.review(tool, 1)["tool"]["revision"]
            )
            async with client(
                "library", "--directory", service.library.directory, "server"
            ) as session:
                assert (await session.list_tools()).tools == []
            assert [row["action"] for row in service.library.history(tool)][-2:] == [
                "rollback",
                "retire",
            ]

        try:
            asyncio.run(exercise())
        finally:
            worker = service._worker
            service.close()
            assert worker.done() and not service._thread.is_alive()
            report["cleanup"].append(dict(layout=layout, worker_exited=True, interface_closed=True))
        journey["matched"] = True
