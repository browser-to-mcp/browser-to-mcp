"""Real Chromium paging, declared budgets, failures, isolation, and MCP contracts."""

import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from time import monotonic
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from paging_site import contract, expected, write_contract

from browser_to_mcp.cli import run
from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow
from browser_to_mcp.tracing import WorkflowTrace
from browser_to_mcp.validation import WorkflowValidationError


def invoke(tmp_path, site, case="full", **options):
    loaded = write_contract(tmp_path / "paging.yaml", contract(site.base, **options))
    result = asyncio.run(replay_workflow(loaded, [f"case={case}"], tmp_path / "traces"))
    return result


def trace_for(result):
    trace = json.loads(Path(result.trace_path).read_text())
    WorkflowTrace.model_validate(trace)
    assert trace["schema_version"] == 3
    return trace


@pytest.mark.parametrize("layout", ["cards", "table", "delay"])
@pytest.mark.parametrize("style", ["query", "path"])
def test_complete_ordered_unique_collection(paging_site, tmp_path, layout, style):
    result = invoke(tmp_path, paging_site, layout=layout, style=style)
    assert result.outputs == {
        "products": expected(paging_site.base),
        "collection": {"pages_read": 2, "complete": True, "stop_reason": "end"},
    }
    pages = trace_for(result)["actions"][-1]["pages"]
    assert [(p["rows_read"], p["records_collected"], p["outcome"]) for p in pages] == [
        (2, 2, "next"),
        (2, 3, "end"),
    ]
    assert paging_site.hits[("full", 1)] == paging_site.hits[("full", 2)] == 1
    assert all(
        set(p) == {"page_number", "rows_read", "records_collected", "outcome", "duration_seconds"}
        for p in pages
    )


@pytest.mark.parametrize(
    "case,order,pages",
    [
        ("single", (0, 1, 2), 1),
        ("empty", (), 1),
        ("within_duplicate", (0, 1, 2), 2),
    ],
)
def test_single_empty_and_duplicate_rows(paging_site, tmp_path, case, order, pages):
    result = invoke(tmp_path, paging_site, case)
    assert result.outputs["products"] == expected(paging_site.base, order)
    assert result.outputs["collection"] == {
        "pages_read": pages,
        "complete": True,
        "stop_reason": "end",
    }


@pytest.mark.parametrize(
    "max_pages,max_items,order,pages,reason",
    [
        (1, 100, (0, 1), 1, "page_limit"),
        (20, 1, (0,), 1, "record_limit"),
        (20, 2, (0, 1), 1, "record_limit"),
        (20, 3, (0, 1, 2), 2, "end"),
        (2, 100, (0, 1, 2), 2, "end"),
    ],
)
def test_limits_are_explicit_and_end_wins_when_proven(
    paging_site, tmp_path, max_pages, max_items, order, pages, reason
):
    result = invoke(tmp_path, paging_site, max_pages=max_pages, max_items=max_items)
    assert result.outputs["products"] == expected(paging_site.base, order)
    assert result.outputs["collection"] == {
        "pages_read": pages,
        "complete": reason == "end",
        "stop_reason": reason,
    }
    assert paging_site.hits[("full", pages + 1)] == 0
    assert trace_for(result)["actions"][-1]["pages"][-1]["outcome"] == reason


def test_caller_page_limit_matches_schema_cli_and_browser(paging_site, tmp_path):
    from browser_to_mcp.replay import parse_input_pairs

    data = contract(paging_site.base, max_pages=2)
    data["inputs"]["max_pages"] = {"type": "integer", "required": False}
    data["steps"][-1]["paging"]["max_pages_input"] = "max_pages"
    loaded = write_contract(tmp_path / "caller.yaml", data)
    compiled = compile_workflow(loaded)
    assert compiled.input_model.model_validate({"case": "full"}).max_pages == 2
    for raw in ("0", "3", "true", "1.5"):
        with pytest.raises(WorkflowExecutionError) as caught:
            parse_input_pairs(["case=full", "max_pages=" + raw], loaded.workflow)
        assert caught.value.category == "input"
        assert not paging_site.hits
    for raw, pages, complete in (([], 2, True), (["max_pages=1"], 1, False)):
        result = asyncio.run(replay_workflow(loaded, ["case=full", *raw], tmp_path / "traces"))
        assert result.outputs["collection"]["pages_read"] == pages
        assert result.outputs["collection"]["complete"] is complete


@pytest.mark.parametrize(
    "mutation", ["missing", "required", "string", "sensitive", "url", "shared"]
)
def test_bad_caller_page_limit_is_rejected_before_browser(tmp_path, mutation):
    data = contract("http://127.0.0.1:1")
    data["inputs"]["max_pages"] = {"type": "integer", "required": False}
    data["steps"][-1]["paging"]["max_pages_input"] = "max_pages"
    if mutation == "missing":
        del data["inputs"]["max_pages"]
    elif mutation in {"required", "sensitive"}:
        data["inputs"]["max_pages"][mutation] = True
    elif mutation == "string":
        data["inputs"]["max_pages"]["type"] = "string"
    elif mutation == "url":
        data["steps"][0]["url"] += "&limit={{max_pages}}"
    else:
        duplicate = copy.deepcopy(data["steps"][-1])
        duplicate["id"] = "second"
        data["steps"].append(duplicate)
    with pytest.raises(WorkflowValidationError):
        write_contract(tmp_path / "bad.yaml", data)


@pytest.mark.parametrize(
    "case",
    [
        "loop",
        "stuck",
        "filters",
        "outside",
        "port",
        "target",
        "secret",
        "fragment",
        "skip",
        "duplicate_page",
        "disabled",
        "button",
        "ambiguous",
        "mixed_end",
        "conflict",
        "broken",
        "late_empty",
        "no_end",
        "404",
        "initial404",
        "redirect",
        "challenge",
        "many",
    ],
)
def test_failures_never_return_partial_success(paging_site, tmp_path, case):
    started = monotonic()
    with pytest.raises(WorkflowExecutionError) as caught:
        invoke(tmp_path, paging_site, case)
    error = caught.value
    assert monotonic() - started < 15
    assert "incomplete" in error.message.lower()
    trace = trace_for(error)
    assert trace["status"] == "failed" and trace["outputs"] == {}
    assert "PRIVATE-TOKEN" not in Path(error.trace_path).read_text()
    if case in {"outside", "secret", "redirect"}:
        assert error.category == "permission"
        assert not list((tmp_path / "traces").glob("*.png"))
    if case not in {"initial404"}:
        assert trace["actions"][-1]["pages"][-1]["outcome"] == "failed"
    assert paging_site.hits[(case, 3)] == 0
    assert paging_site.hits["forbidden"] == 0
    if case in {
        "stuck",
        "filters",
        "outside",
        "port",
        "target",
        "secret",
        "fragment",
        "skip",
        "duplicate_page",
        "disabled",
        "button",
        "ambiguous",
        "mixed_end",
    }:
        assert paging_site.hits[(case, 2)] == 0


@pytest.mark.parametrize("budget", ["paging", "overall"])
def test_deadlines_preserve_progress_and_return_no_data(paging_site, tmp_path, budget):
    data = contract(paging_site.base)
    data["steps"][-1]["timeout_seconds"] = 10
    data["steps"][-1]["paging"]["timeout_seconds"] = 1 if budget == "paging" else 20
    loaded = write_contract(tmp_path / "paging.yaml", data)
    with pytest.raises(WorkflowExecutionError) as caught:
        asyncio.run(
            replay_workflow(
                loaded,
                ["case=slow"],
                tmp_path / "traces",
                timeout_seconds=2 if budget == "overall" else 30,
            )
        )
    trace = trace_for(caught.value)
    assert trace["outputs"] == {} and "incomplete" in caught.value.message.lower()
    pages = trace["actions"][-1]["pages"]
    assert pages[0]["outcome"] == "next" and pages[0]["records_collected"] == 2
    assert pages[-1]["outcome"] in {"failed", "cancelled"}
    assert paging_site.hits[("slow", 3)] == 0


def test_cancellation_preserves_trace_and_closes_context(paging_site, tmp_path, monkeypatch):
    from playwright.async_api import BrowserContext

    closed = []
    original = BrowserContext.close

    async def close(context, *args, **kwargs):
        await original(context, *args, **kwargs)
        closed.append(context)

    monkeypatch.setattr(BrowserContext, "close", close)
    data = contract(paging_site.base)
    data["steps"][-1]["timeout_seconds"] = 10
    loaded = write_contract(tmp_path / "paging.yaml", data)

    async def cancel():
        task = asyncio.create_task(replay_workflow(loaded, ["case=slow"], tmp_path / "traces"))
        try:
            async with asyncio.timeout(10):
                while not paging_site.second_page.is_set():
                    await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(cancel())
    assert len(closed) == 1
    files = list((tmp_path / "traces").glob("*.json"))
    assert len(files) == 1
    trace = json.loads(files[0].read_text())
    assert trace["outputs"] == {} and trace["status"] == "failed"
    assert trace["actions"][-1]["pages"][-1]["outcome"] == "cancelled"
    assert paging_site.hits[("slow", 3)] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "zero_pages",
        "too_many_pages",
        "bool_pages",
        "no_deadline",
        "too_long",
        "both_styles",
        "no_style",
        "bad_path",
        "template_selector",
        "object",
        "no_key",
        "keep",
        "status_missing",
        "status_wrong_type",
        "status_nullable",
        "status_extra",
        "status_collision",
        "two_owners",
        "wrong_action",
        "cursor",
    ],
)
def test_invalid_contracts_never_start_browser(tmp_path, monkeypatch, mutation):
    data = contract("http://127.0.0.1:8767")
    step = data["steps"][-1]
    paging = step["paging"]
    if mutation == "zero_pages":
        paging["max_pages"] = 0
    if mutation == "too_many_pages":
        paging["max_pages"] = 21
    if mutation == "bool_pages":
        paging["max_pages"] = True
    if mutation == "no_deadline":
        del paging["timeout_seconds"]
    if mutation == "too_long":
        paging["timeout_seconds"] = 301
    if mutation == "both_styles":
        paging["page_path"] = "/page/{page}"
    if mutation == "no_style":
        del paging["page_parameter"]
    if mutation == "bad_path":
        del paging["page_parameter"]
        paging["page_path"] = "//outside/{page}"
    if mutation == "template_selector":
        paging["next_selector"] = "{{case}}"
    if mutation == "object":
        data["outputs"]["products"]["type"] = "object"
    if mutation == "no_key":
        del step["record"]["unique_by"]
    if mutation == "keep":
        step["record"]["duplicates"] = "keep"
    if mutation == "status_missing":
        del data["outputs"]["collection"]
    if mutation == "status_wrong_type":
        data["outputs"]["collection"]["properties"]["complete"]["type"] = "string"
    if mutation == "status_nullable":
        data["outputs"]["collection"]["properties"]["complete"]["nullable"] = True
    if mutation == "status_extra":
        data["outputs"]["collection"]["properties"]["extra"] = {"type": "string"}
    if mutation == "status_collision":
        paging["status_output"] = "products"
    if mutation == "two_owners":
        other = copy.deepcopy(step)
        other["id"] = "other"
        data["steps"].append(other)
        data["outputs"]["other"] = copy.deepcopy(data["outputs"]["products"])
    if mutation == "wrong_action":
        data["steps"][0]["paging"] = paging
    if mutation == "cursor":
        paging["cursor"] = "guess"
    browser = Mock(side_effect=AssertionError("Invalid contracts cannot start Chromium"))
    monkeypatch.setattr("browser_to_mcp.replay.async_playwright", browser)
    with pytest.raises(WorkflowValidationError):
        write_contract(tmp_path / "invalid.yaml", data)
    browser.assert_not_called()


def test_independent_concurrent_inputs_have_no_shared_paging_state(paging_site, tmp_path):
    loaded = write_contract(tmp_path / "paging.yaml", contract(paging_site.base))

    async def concurrent():
        return await asyncio.gather(
            *(
                replay_workflow(loaded, [f"case={case}"], tmp_path / "traces")
                for case in ("full", "empty", "single")
            )
        )

    full, empty, single = asyncio.run(concurrent())
    assert full.outputs["products"] == single.outputs["products"] == expected(paging_site.base)
    assert empty.outputs["products"] == []
    assert [r.outputs["collection"]["pages_read"] for r in (full, empty, single)] == [2, 1, 1]
    assert len({r.trace_path for r in (full, empty, single)}) == 3


@pytest.mark.parametrize("transport", ["cli", "mcp"])
def test_real_cli_mcp_and_trace_agree(paging_site, tmp_path, capsys, transport):
    folder = tmp_path / "workflows"
    folder.mkdir()
    loaded = write_contract(folder / "paging.yaml", contract(paging_site.base, max_pages=1))
    traces = tmp_path / "traces"
    if transport == "cli":
        assert (
            run(
                [
                    "replay",
                    str(folder / "paging.yaml"),
                    "--input",
                    "case=full",
                    "--trace-dir",
                    str(traces),
                ]
            )
            == 0
        )
        response = json.loads(capsys.readouterr().out)
    else:

        async def invoke_mcp():
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-c",
                    "from browser_to_mcp.cli import main; main()",
                    "server",
                    "--workflow-dir",
                    str(folder),
                    "--trace-dir",
                    str(traces),
                ],
                env={k: os.environ[k] for k in ["PLAYWRIGHT_BROWSERS_PATH"] if k in os.environ},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert tools.tools[0].outputSchema == compile_workflow(loaded).output_schema
                    result = await client.call_tool("collect_catalog", {"case": "full"})
                    empty = await client.call_tool("collect_catalog", {"case": "empty"})
                    bad = await client.call_tool("collect_catalog", {"case": "filters"})
                    assert not result.isError and not empty.isError and bad.isError
                    assert empty.structuredContent["outputs"]["collection"]["complete"] is True
                    assert "incomplete" in json.dumps(bad.structuredContent)
                    return result.structuredContent

        response = asyncio.run(invoke_mcp())
    assert response["outputs"] == {
        "products": expected(paging_site.base, (0, 1)),
        "collection": {"pages_read": 1, "complete": False, "stop_reason": "page_limit"},
    }
    model = compile_workflow(loaded).output_model
    model.model_validate({key: response[key] for key in model.model_fields})
    trace = json.loads(Path(response["trace_path"]).read_text())
    assert trace["outputs"] == response["outputs"]
    assert trace["metadata"]["manifest_digest"] == loaded.manifest_digest


def test_twenty_page_budget_stops_continuously_changing_results(paging_site, tmp_path):
    result = invoke(tmp_path, paging_site, "changing")
    assert result.outputs["collection"] == {
        "pages_read": 20,
        "complete": False,
        "stop_reason": "page_limit",
    }
    assert len(result.outputs["products"]) == 20
    assert [r["title"] for r in result.outputs["products"]] == [f"Item {n}" for n in range(3, 23)]
    assert paging_site.hits[("changing", 21)] == 0
    assert len(trace_for(result)["actions"][-1]["pages"]) == 20


def test_record_limit_on_final_page_is_still_incomplete(paging_site, tmp_path):
    result = invoke(tmp_path, paging_site, "single", max_items=2)
    assert result.outputs["products"] == expected(paging_site.base, (0, 1))
    assert result.outputs["collection"] == {
        "pages_read": 1,
        "complete": False,
        "stop_reason": "record_limit",
    }


def test_query_values_preserve_duplicates_blank_and_encoded_filters(tmp_path):
    from browser_to_mcp.paging import check_page_url
    from browser_to_mcp.structured_outputs import OutputValidationError

    loaded = write_contract(tmp_path / "paging.yaml", contract("http://127.0.0.1"))
    step = loaded.workflow.steps[-1]
    initial = "http://127.0.0.1/catalog?category=Tea+%26+Coffee&tag=a&tag=b&empty=&page=1"
    next_url = "http://127.0.0.1/catalog?page=2&empty=&tag=b&tag=a&category=Tea%20%26%20Coffee"
    check_page_url(initial, next_url, 2, step, ["127.0.0.1"])
    for bad in (
        next_url.replace("&empty=", ""),
        next_url.replace("&tag=b", ""),
        next_url.replace("%26", "%2526"),
        next_url + "&tag=a",
    ):
        with pytest.raises(OutputValidationError):
            check_page_url(initial, bad, 2, step, ["127.0.0.1"])
    with pytest.raises(OutputValidationError):
        check_page_url(next_url, next_url, 1, step, ["127.0.0.1"])


def test_combined_size_limit_is_checked_across_pages(paging_site, tmp_path, monkeypatch):
    # Each page fits; all three unique records together exceed the aggregate bound.
    from browser_to_mcp import paging

    rows = expected(paging_site.base)

    def size(value):
        return len(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())

    monkeypatch.setattr(paging, "MAX_RECORD_BYTES", max(size(rows[:2]), size(rows[1:])))
    with pytest.raises(WorkflowExecutionError) as caught:
        invoke(tmp_path, paging_site)
    assert "combined result" in caught.value.message
    trace = trace_for(caught.value)
    assert trace["outputs"] == {} and trace["actions"][-1]["pages"][-1]["outcome"] == "failed"


def test_sensitive_paged_values_stay_out_of_trace_and_screenshots(paging_site, tmp_path):
    data = contract(paging_site.base)
    data["outputs"]["products"]["items"]["properties"]["title"]["sensitive"] = True
    loaded = write_contract(tmp_path / "paging.yaml", data)
    result = asyncio.run(replay_workflow(loaded, ["case=full"], tmp_path / "traces"))
    assert result.outputs["products"] == expected(paging_site.base)
    text = Path(result.trace_path).read_text()
    assert "Notebook" not in text and "[REDACTED]" in text
    with pytest.raises(WorkflowExecutionError):
        asyncio.run(replay_workflow(loaded, ["case=conflict"], tmp_path / "traces"))
    assert not list((tmp_path / "traces").glob("*.png"))


def test_paged_number_boolean_and_null_keep_their_types(paging_site, tmp_path):
    data = contract(paging_site.base)
    fields = data["steps"][-1]["record"]["fields"]
    props = data["outputs"]["products"]["items"]["properties"]
    for name, kind in (("count", "integer"), ("amount", "number"), ("available", "boolean")):
        fields[name] = {"selector": "." + name}
        props[name] = {"type": kind}
    fields["note"] = {"selector": ".missing"}
    props["note"] = {"type": "string", "nullable": True}
    loaded = write_contract(tmp_path / "paging.yaml", data)
    result = asyncio.run(replay_workflow(loaded, ["case=typed"], tmp_path / "traces"))
    for row in result.outputs["products"]:
        assert row["count"] == 0 and type(row["count"]) is int
        assert row["amount"] == 0.0 and type(row["amount"]) is float
        assert row["available"] is False and row["note"] is None
