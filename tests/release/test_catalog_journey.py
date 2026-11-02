"""The shared catalog itself crosses the builder, publication and real STDIO boundaries."""

import asyncio
from importlib.metadata import version
from pathlib import Path

import pytest
from builder_site import serve
from test_release_journey import build, catalog_outputs, client

from browser_to_mcp.evaluation.service import _exact
from browser_to_mcp.interface.service import Interface
from browser_to_mcp.tracing import WorkflowTrace


@pytest.mark.parametrize("layout", ["books", "laptops", "clothing"])
def test_complete_shared_catalog_journey(layout, shared_catalog_report):
    report, path = shared_catalog_report
    report["mcp_sdk_version"] = version("mcp")
    root = path.parent / path.stem / layout
    journey = dict(layout=layout, matched=False)
    report["journeys"].append(journey)
    with serve() as (site, base):
        service = Interface(root / "ui", root / "jobs", root / "library")

        async def exercise():
            async with client("jobs", "--directory", service.jobs.directory, "server") as jobs:
                for attempt in range(1, 4):
                    review = await build(
                        service, jobs, base, layout, attempt, report, catalog_site=site
                    )
            builds = [b for b in report["builds"] if b["layout"] == layout]
            assert len({b["candidate_sha256"] for b in builds}) == 3
            async with client(
                "library",
                "--directory",
                service.library.directory,
                "server",
                "--trace-dir",
                root / "traces",
                "--catalog",
                f"{layout}=collect_{layout}",
            ) as session:
                tool = next(
                    t for t in (await session.list_tools()).tools if t.name == "collect_catalog"
                )
                assert tool.inputSchema["properties"]["catalog"]["const"] == layout
                for case in ("full", "limited", "empty"):
                    site.mode = "empty" if case == "empty" else "normal"
                    inputs = dict(catalog=layout, **({"max_pages": 1} if case == "limited" else {}))
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
                        started = asyncio.get_running_loop().time()
                        try:
                            response = await session.call_tool("collect_catalog", inputs)
                            assert not response.isError, response
                            assert _exact(
                                response.structuredContent, catalog_outputs(base, layout, case)
                            )
                            meta = response.meta
                            trace = WorkflowTrace.model_validate_json(
                                Path(meta["trace_path"]).read_bytes()
                            )
                            assert trace.status == "completed"
                            assert _exact(
                                trace.outputs["result"], response.structuredContent["items"]
                            )
                            assert _exact(
                                trace.outputs["collection"],
                                {
                                    key: response.structuredContent[key]
                                    for key in ("pages_read", "complete", "stop_reason")
                                },
                            )
                            assert (
                                trace.metadata.manifest_digest
                                == meta["manifest_digest"]
                                == review["version"]["manifest"]
                            )
                            assert trace.metadata.publication == meta["publication"]
                            assert meta["publication"]["version"] == 3
                            assert meta["publication"]["tool_id"] == review["tool"]["id"]
                            observation.update(
                                matched=True,
                                trace_path=meta["trace_path"],
                                run_id=trace.metadata.run_id,
                                browser_version=trace.metadata.browser_version,
                                browser_seconds=meta["duration_seconds"],
                            )
                        finally:
                            observation["seconds"] = round(
                                asyncio.get_running_loop().time() - started, 3
                            )
                for inputs in ({}, {"catalog": "unknown"}, {"catalog": layout, "max_pages": 0}):
                    before, traces = list(site.hits), set((root / "traces").glob("*.json"))
                    response = await session.call_tool("collect_catalog", inputs)
                    assert response.isError
                    assert site.hits == before and set((root / "traces").glob("*.json")) == traces
                    report["refusals"].append(dict(layout=layout, inputs=inputs, matched=True))

        try:
            asyncio.run(exercise())
        finally:
            worker = service._worker
            service.close()
            assert worker.done() and not service._thread.is_alive()
            report["cleanup"].append(dict(layout=layout, worker_exited=True, interface_closed=True))
        journey["matched"] = True
