"""The shared interface uses fixed, tested workflows and the normal browser policy."""

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from builder_site import expected, proposal, serve
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

import browser_to_mcp.evaluation.service as evaluation_service
from browser_to_mcp.cli import run
from browser_to_mcp.evaluation.service import digest, evaluate
from browser_to_mcp.library.store import Library
from browser_to_mcp.loader import Publication, load_workflow
from browser_to_mcp.replay import replay_workflow
from browser_to_mcp.server import ServerConfigurationError, create_server


@pytest.fixture
def catalogs(tmp_path):
    with serve() as (site, base):
        loaded = []
        paths = []
        for layout in ("books", "laptops", "clothing"):
            data = yaml.safe_load(Path("examples/paging/catalog.yaml").read_text())
            data["name"] = "catalog_" + layout
            data["inputs"] = {"max_pages": {"type": "integer", "required": False}}
            data["steps"][0]["url"] = f"{base}/{layout}/?q=notebook"
            step = data["steps"][1]
            value = proposal(layout)
            step["selector"] = value["area"]
            step["record"] = value["record"]
            step["paging"]["max_pages_input"] = "max_pages"
            path = tmp_path / (layout + ".candidate")
            path.write_text(yaml.safe_dump(data))
            paths.append(path)
            loaded.append(replace(load_workflow(path), publication=Publication(layout, 1, "test")))
        yield site, base, loaded, paths


def server(loaded, tmp_path):
    return create_server(
        loaded,
        tmp_path / "traces",
        catalogs=[f"{name}=catalog_{name}" for name in ("books", "laptops", "clothing")],
    )


@pytest.mark.parametrize("layout", ["books", "laptops", "clothing"])
def test_exact_results_and_page_limit(catalogs, tmp_path, layout):
    site, base, loaded, _ = catalogs

    async def exercise():
        app = server(loaded, tmp_path)
        tools = await app.list_tools()
        tool = next(t for t in tools if t.name == "collect_catalog")
        assert tool.inputSchema["additionalProperties"] is False
        assert tool.inputSchema["properties"]["catalog"]["enum"] == ["books", "laptops", "clothing"]
        assert tool.inputSchema["properties"]["max_pages"]["default"] == 20
        for limit in (None, 1, 20):
            result = await app.call_tool(
                "collect_catalog", {"catalog": layout, **({"max_pages": limit} if limit else {})}
            )
            assert not result.isError
            assert result.structuredContent == dict(
                catalog=layout,
                items=expected(base, (0, 1) if limit == 1 else (0, 1, 2)),
                pages_read=1 if limit == 1 else 2,
                complete=limit != 1,
                stop_reason="page_limit" if limit == 1 else "end",
            )
            assert result.meta["publication"]["tool_id"] == layout
            assert (
                result.meta["manifest_digest"]
                == loaded[("books", "laptops", "clothing").index(layout)].manifest_digest
            )
            assert Path(result.meta["trace_path"]).is_file()
        assert all(path.startswith(f"/{layout}/") for path in site.hits)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"catalog": ""},
        {"catalog": "Books"},
        {"catalog": "unknown"},
        {"catalog": True},
        {"catalog": ["books"]},
        {"catalog": "books", "url": "https://x"},
        *(
            {"catalog": "books", "max_pages": value}
            for value in (0, 21, True, False, "2", 1.5, None)
        ),
    ],
)
def test_bad_inputs_never_open_browser(catalogs, tmp_path, arguments):
    site, _, loaded, _ = catalogs
    with pytest.raises(ToolError):
        asyncio.run(server(loaded, tmp_path).call_tool("collect_catalog", arguments))
    assert site.hits == []


@pytest.mark.parametrize(
    "mode",
    [
        "broken",
        "blank_price",
        "cart",
        "conflict",
        "loop",
        "missing_page",
        "challenge",
        "unmarked_empty",
        "slow",
    ],
)
def test_failures_do_not_return_partial_catalog(catalogs, tmp_path, mode):
    site, _, loaded, _ = catalogs
    site.mode = mode
    response = asyncio.run(
        server(loaded, tmp_path).call_tool("collect_catalog", {"catalog": "books"})
    )
    assert response.isError
    assert "items" not in response.structuredContent
    assert response.structuredContent["trace_path"]
    assert response.meta["publication"]["tool_id"] == "books"


def test_record_limit_is_a_failure(catalogs, tmp_path):
    _, _, loaded, paths = catalogs
    data = yaml.safe_load(paths[0].read_text())
    data["outputs"]["products"]["max_items"] = 2
    paths[0].write_text(yaml.safe_dump(data))
    loaded[0] = replace(load_workflow(paths[0]), publication=loaded[0].publication)
    response = asyncio.run(
        server(loaded, tmp_path).call_tool("collect_catalog", {"catalog": "books"})
    )
    assert response.isError
    assert response.structuredContent["category"] == "assertion"
    assert "items" not in response.structuredContent


@pytest.mark.parametrize(
    "bindings",
    [
        ["books=missing"],
        ["other=catalog_books"],
        ["books=catalog_books", "books=catalog_laptops"],
        ["books"],
    ],
)
def test_bad_configuration_refused(catalogs, bindings):
    with pytest.raises(ServerConfigurationError):
        create_server(catalogs[2], catalogs=bindings)
    assert not catalogs[0].hits


def test_unpublished_and_incompatible_workflows_refused(catalogs):
    _, _, loaded, paths = catalogs
    with pytest.raises(ServerConfigurationError, match="published"):
        create_server([load_workflow(paths[0])], catalogs=["books=catalog_books"])
    data = yaml.safe_load(paths[0].read_text())
    data["inputs"]["query"] = {"type": "string", "required": False}
    paths[0].write_text(yaml.safe_dump(data))
    incompatible = replace(load_workflow(paths[0]), publication=loaded[0].publication)
    with pytest.raises(ServerConfigurationError, match="max_pages"):
        create_server([incompatible], catalogs=["books=catalog_books"])


def test_cli_refuses_unavailable_catalog(tmp_path, capsys):
    assert (
        run(["library", "--directory", str(tmp_path), "server", "--catalog", "books=missing"]) == 2
    )
    assert "catalog" in capsys.readouterr().err


def test_published_catalog_over_stdio(catalogs, tmp_path, monkeypatch):
    site, base, _, paths = catalogs
    plan = dict(
        name="Fixed catalog",
        mode="controlled",
        requirements_source="THREE_SITE_PILOT.md",
        expectations_source="Independent notebook answers",
        timeout_seconds=5,
        not_applicable={"combined_filters": "Only one input"},
        cases=[],
    )
    for name, inputs, covers, indexes, pages, complete in (
        ("full", {"max_pages": 20}, ["demonstration", "multiple_pages"], (0, 1, 2), 2, True),
        ("limited", {"max_pages": 1}, ["changed_inputs"], (0, 1), 1, False),
        ("empty", {}, ["zero_results"], (), 1, True),
    ):
        case = dict(
            id=name,
            inputs=inputs,
            covers=covers,
            expected=dict(
                outcome="outputs",
                outputs=dict(
                    products=expected(base, indexes),
                    collection=dict(
                        pages_read=pages,
                        complete=complete,
                        stop_reason="end" if complete else "page_limit",
                    ),
                ),
            ),
        )
        plan["cases"].append(case)
    plan["cases"] += [
        dict(
            id="invalid",
            inputs={"max_pages": 0},
            covers=["invalid_input"],
            expected=dict(outcome="error", category="input", path="inputs"),
        ),
        dict(
            id="broken",
            inputs={},
            covers=["changed_page"],
            expected=dict(
                outcome="error", category="assertion", path="outputs.products.0.price_text"
            ),
        ),
    ]
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    pin = digest(plan_path.read_bytes())
    evidence = tmp_path / "evidence"
    modes = iter(("normal", "normal", "empty", "broken"))

    async def replay_fixture(*args, **kwargs):
        # Change only the controlled page; execute and verify the real workflow.
        site.mode = next(modes)
        return await replay_workflow(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(evaluation_service, "replay_workflow", replay_fixture)
        report = asyncio.run(evaluate(paths[0], plan_path, evidence, pin))
    site.mode = "normal"
    assert report.eligible, report.blockers
    library = Library(tmp_path / "library")
    review = library.import_draft(paths[0], origin="controlled catalog test")
    tool_id = review["tool"]["id"]
    review = library.attach_tests(tool_id, 1, evidence, pin)
    library.activate(
        tool_id,
        1,
        revision=review["tool"]["revision"],
        candidate_sha256=review["version"]["sha"],
        approve=True,
    )

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-c",
                "from browser_to_mcp.cli import main; main()",
                "library",
                "--directory",
                str(library.directory),
                "server",
                "--catalog",
                "books=catalog_books",
                "--trace-dir",
                str(tmp_path / "traces"),
            ],
            env={k: os.environ[k] for k in ("PLAYWRIGHT_BROWSERS_PATH",) if k in os.environ},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for limit in (1, 20):
                    result = await session.call_tool(
                        "collect_catalog", {"catalog": "books", "max_pages": limit}
                    )
                    assert not result.isError, result
                    assert result.structuredContent["items"] == expected(
                        base, (0, 1) if limit == 1 else (0, 1, 2)
                    )
                    assert result.meta["publication"]["tool_id"] == tool_id
                before = len(site.hits)
                assert (await session.call_tool("collect_catalog", {"catalog": "laptops"})).isError
                assert len(site.hits) == before

    asyncio.run(exercise())
    library.retire(tool_id, revision=library.review(tool_id, 1)["tool"]["revision"])
    with pytest.raises(ServerConfigurationError):
        create_server(library.published(), catalogs=["books=catalog_books"])


@pytest.mark.parametrize("layout", ["books", "laptops", "clothing"])
@pytest.mark.parametrize("mode", ["empty", "same_title"])
def test_empty_and_distinct_products(catalogs, tmp_path, layout, mode):
    site, base, loaded, _ = catalogs
    site.mode = mode
    response = asyncio.run(
        server(loaded, tmp_path).call_tool("collect_catalog", {"catalog": layout})
    )
    assert not response.isError
    rows = [] if mode == "empty" else [dict(row, title="Same title") for row in expected(base)]
    assert response.structuredContent == dict(
        catalog=layout,
        items=rows,
        pages_read=1 if mode == "empty" else 2,
        complete=True,
        stop_reason="end",
    )


def test_wrong_destination_never_contacted(catalogs, tmp_path):
    site, _, loaded, _ = catalogs
    with serve() as (outside, outside_url):
        site.mode, site.outside_url = "outside", outside_url
        response = asyncio.run(
            server(loaded, tmp_path).call_tool("collect_catalog", {"catalog": "books"})
        )
        assert response.isError
        assert not outside.hits
        assert "items" not in response.structuredContent


def test_cancelled_call_releases_shared_slot(catalogs, tmp_path):
    site, base, loaded, _ = catalogs

    async def exercise():
        app = server(loaded, tmp_path)
        site.mode = "slow"
        pending = asyncio.create_task(app.call_tool("collect_catalog", {"catalog": "books"}))
        async with asyncio.timeout(10):
            while len(site.hits) < 2:
                await asyncio.sleep(0.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        site.mode = "normal"
        result = await app.call_tool("collect_catalog", {"catalog": "laptops"})
        assert not result.isError
        assert result.structuredContent["items"] == expected(base)
        traces = [json.loads(path.read_text()) for path in (tmp_path / "traces").glob("*.json")]
        assert len(traces) == 2

    asyncio.run(exercise())
