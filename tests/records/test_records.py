"""Typed contract, aligned rows, privacy, CLI, and actual MCP results."""

import asyncio
import copy
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError
from record_site import contract, expected, write_contract

from browser_to_mcp.cli import run
from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.models import StructuredOutput
from browser_to_mcp.replay import WorkflowExecutionError, process_outputs, replay_workflow
from browser_to_mcp.tracing import WorkflowTrace, redact_outputs
from browser_to_mcp.validation import WorkflowValidationError


@pytest.mark.parametrize("kind", ["array", "object"])
def test_strict_generated_record_schema_rejects_wrong_types_and_extra_keys(tmp_path, kind):
    loaded = write_contract(
        tmp_path / "rows.yaml",
        contract("https://example.com", kind=kind)
        | {"permissions": {"allowed_domains": ["example.com"]}},
    )
    compiled = compile_workflow(loaded)
    rows = expected("https://example.com")
    envelope = dict(
        workflow="catalog_rows",
        manifest_digest=loaded.manifest_digest,
        outputs={"products": rows if kind == "array" else rows[0]},
        trace_path=None,
        duration_seconds=0.0,
    )
    assert (
        compiled.output_model.model_validate(envelope).model_dump()["outputs"]
        == envelope["outputs"]
    )
    for key, bad in [
        ("count", True),
        ("available", 0),
        ("amount", float("inf")),
        ("title", ""),
        ("title", "x" * 4097),
        ("extra", "wrong"),
    ]:
        changed = copy.deepcopy(envelope)
        record = changed["outputs"]["products"]
        if kind == "array":
            record = record[0]
        record[key] = bad
        with pytest.raises(ValidationError):
            compiled.output_model.model_validate(changed)
    missing = copy.deepcopy(envelope)
    record = missing["outputs"]["products"]
    if kind == "array":
        record = record[0]
    del record["note"]  # nullable means explicit null, not an omitted key
    with pytest.raises(ValidationError):
        compiled.output_model.model_validate(missing)


@pytest.mark.parametrize(
    "mutation",
    [
        "nested",
        "no_fields",
        "too_many_fields",
        "max_items",
        "no_empty_marker",
        "no_empty_assertion",
        "missing_field",
        "nullable_key",
        "wrong_source_type",
        "unknown_code",
        "nullable_flag",
        "empty_flag",
        "whitespace_flag",
    ],
)
def test_bad_record_contract_never_starts_browser(tmp_path, monkeypatch, mutation):
    data = contract("https://example.com", allow_empty=True)
    data["permissions"]["allowed_domains"] = ["example.com"]
    output = data["outputs"]["products"]
    props = output["items"]["properties"]
    extraction = data["steps"][-1]["record"]
    if mutation == "nested":
        props["title"]["type"] = "object"
    if mutation == "no_fields":
        output["items"]["properties"] = {}
    if mutation == "too_many_fields":
        props.update({f"f{i}": {"type": "string"} for i in range(17)})
    if mutation == "max_items":
        output["max_items"] = 101
    if mutation == "no_empty_marker":
        del extraction["empty_selector"]
    if mutation == "no_empty_assertion":
        data["success"][0]["assertion"] = "output_not_empty"
    if mutation == "missing_field":
        del extraction["fields"]["title"]
    if mutation == "nullable_key":
        props["product_url"]["nullable"] = True
    if mutation == "wrong_source_type":
        props["product_url"]["type"] = "integer"
    if mutation == "unknown_code":
        extraction["fields"]["title"]["script"] = "PRIVATE-JS"
    if mutation == "nullable_flag":
        props["title"]["nullable"] = "false"
    if mutation == "empty_flag":
        output["allow_empty"] = "true"
    if mutation == "whitespace_flag":
        extraction["fields"]["title"]["normalize_whitespace"] = 1
    browser = Mock(side_effect=AssertionError("Invalid contracts cannot start a browser"))
    monkeypatch.setattr("browser_to_mcp.replay.async_playwright", browser)
    with pytest.raises(WorkflowValidationError):
        write_contract(tmp_path / "bad.yaml", data)
    browser.assert_not_called()


@pytest.mark.parametrize(
    "case,order",
    [("normal", (0, 1, 2)), ("order", (1, 2, 0)), ("duplicate", (0, 1, 2)), ("delay", (0, 1, 2))],
)
def test_real_rows_types_alignment_order_and_redacted_trace(records_site, tmp_path, case, order):
    loaded = write_contract(tmp_path / "rows.yaml", contract(records_site.base))
    result = asyncio.run(replay_workflow(loaded, [f"case={case}"], tmp_path / "traces"))
    assert result.outputs == {"products": expected(records_site.base, order)}
    for row in result.outputs["products"]:
        assert type(row["count"]) is int and type(row["amount"]) is float
        assert type(row["available"]) is bool
    trace = json.loads(Path(result.trace_path).read_text())
    WorkflowTrace.model_validate(trace)
    assert trace["schema_version"] == 2
    assert trace["outputs"] == redact_outputs(result.outputs, loaded.workflow, {})
    assert "PRIVATE-NOTE" not in Path(result.trace_path).read_text()
    assert trace["outputs"]["products"][0]["note"] == "[REDACTED]"
    assert records_site.hits["/cart"] == 0
    assert not any(p.startswith("/products/") for p in records_site.hits)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "ambiguous",
        "bad_number",
        "non_finite",
        "conflict",
        "blank",
        "cart_link",
        "outside_link",
        "secret_link",
        "huge_cell",
        "nested",
        "mixed_empty",
        "unmarked",
        "missing_area",
        "duplicate_area",
        "too_many",
        "byte_limit",
        "hidden",
    ],
)
def test_broken_rows_never_become_partial_success(records_site, tmp_path, case):
    data = contract(records_site.base, allow_empty=True)
    if case == "byte_limit":
        data["outputs"]["products"]["max_items"] = 100
    loaded = write_contract(tmp_path / "rows.yaml", data)
    with pytest.raises(WorkflowExecutionError) as caught:
        asyncio.run(replay_workflow(loaded, [f"case={case}"], tmp_path / "traces"))
    error = caught.value
    assert str(error.category) in {"assertion", "runtime"}
    assert "PRIVATE" not in error.message
    trace = json.loads(Path(error.trace_path).read_text())
    assert trace["status"] == "failed" and trace["outputs"] == {}
    assert trace["screenshots"] == []
    assert not list((tmp_path / "traces").glob("*.png"))


def test_confirmed_empty_list_requires_explicit_contract(records_site, tmp_path):
    loaded = write_contract(tmp_path / "rows.yaml", contract(records_site.base, allow_empty=True))
    result = asyncio.run(replay_workflow(loaded, ["case=empty"], tmp_path / "traces"))
    assert result.outputs == {"products": []}
    assert json.loads(Path(result.trace_path).read_text())["outputs"] == result.outputs
    data = contract(records_site.base)
    loaded = write_contract(tmp_path / "noempty.yaml", data)
    with pytest.raises(WorkflowExecutionError):
        asyncio.run(replay_workflow(loaded, ["case=empty"]))


def test_one_record_requires_exactly_one_row(records_site, tmp_path):
    loaded = write_contract(tmp_path / "one.yaml", contract(records_site.base, kind="object"))
    result = asyncio.run(replay_workflow(loaded, ["case=single"]))
    assert result.outputs == {"products": expected(records_site.base)[0]}
    with pytest.raises(WorkflowExecutionError):
        asyncio.run(replay_workflow(loaded, ["case=normal"]))


@pytest.mark.parametrize("duplicates,count", [("keep", 4), ("reject", None)])
def test_duplicate_rule_is_reviewed(records_site, tmp_path, duplicates, count):
    data = contract(records_site.base)
    data["steps"][-1]["record"]["duplicates"] = duplicates
    if duplicates == "keep":
        del data["steps"][-1]["record"]["unique_by"]
    loaded = write_contract(tmp_path / "rows.yaml", data)
    if count is None:
        with pytest.raises(WorkflowExecutionError):
            asyncio.run(replay_workflow(loaded, ["case=duplicate"]))
    else:
        result = asyncio.run(replay_workflow(loaded, ["case=duplicate"]))
        assert len(result.outputs["products"]) == count


@pytest.mark.parametrize("entry", ["cli", "mcp"])
def test_public_protocols_return_exact_records_and_trace(records_site, tmp_path, capsys, entry):
    folder = tmp_path / "workflows"
    folder.mkdir()
    loaded = write_contract(folder / "rows.yaml", contract(records_site.base, allow_empty=True))
    traces = tmp_path / "traces"
    if entry == "cli":
        assert (
            run(
                [
                    "replay",
                    str(folder / "rows.yaml"),
                    "--input",
                    "case=normal",
                    "--trace-dir",
                    str(traces),
                ]
            )
            == 0
        )
        response = json.loads(capsys.readouterr().out)
    else:

        async def invoke():
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
                    good = await client.call_tool("catalog_rows", {"case": "normal"})
                    empty = await client.call_tool("catalog_rows", {"case": "empty"})
                    bad = await client.call_tool("catalog_rows", {"case": "missing"})
                    assert not empty.isError and empty.structuredContent["outputs"] == {
                        "products": []
                    }
                    assert bad.isError and bad.structuredContent["category"] == "assertion"
                    assert "PRIVATE" not in json.dumps(bad.structuredContent)
                    assert not good.isError
                    return good.structuredContent

        response = asyncio.run(invoke())
    assert response["outputs"] == {"products": expected(records_site.base)}
    trace = json.loads(Path(response["trace_path"]).read_text())
    assert trace["outputs"] == redact_outputs(response["outputs"], loaded.workflow, {})
    assert trace["metadata"]["manifest_digest"] == loaded.manifest_digest
    assert not list(traces.glob("*.png"))


def test_total_size_nulls_and_closed_shape_checked_without_browser(tmp_path):
    data = contract("https://example.com")
    data["permissions"]["allowed_domains"] = ["example.com"]
    loaded = write_contract(tmp_path / "rows.yaml", data)
    raw = {
        key: str(value).lower()
        if isinstance(value, bool)
        else str(value)
        if value is not None
        else None
        for key, value in expected("https://example.com")[0].items()
    }
    converted = process_outputs(loaded.workflow, {"products": [raw]})
    assert converted["products"][0]["count"] == 0
    for mutation in [
        dict(raw, extra="no"),
        {k: v for k, v in raw.items() if k != "count"},
        dict(raw, count=None),
    ]:
        with pytest.raises(WorkflowExecutionError):
            process_outputs(loaded.workflow, {"products": [mutation]})
    assert isinstance(loaded.workflow.outputs["products"], StructuredOutput)


def test_scalar_manifest_identities_are_unchanged():
    root = Path(__file__).parents[2]
    assert (
        load_workflow(root / "workflows/hn_search.yaml").manifest_digest
        == "sha256:390bfa5a539181f66cfbea7e3386eb6ed4e48858679e8587fd3e34861107e86f"
    )
    assert (
        load_workflow(root / "workflows/wikipedia_search.yaml").manifest_digest
        == "sha256:ba5f137fbb9d3804f3aea8b16a91d274690c90bb586749cc90ab3ccc7d4fe414"
    )


@pytest.mark.parametrize("case", ["same_title", "unicode"])
def test_titles_do_not_join_unrelated_rows_or_lose_unicode(records_site, tmp_path, case):
    loaded = write_contract(tmp_path / "rows.yaml", contract(records_site.base))
    result = asyncio.run(replay_workflow(loaded, [f"case={case}"]))
    rows = expected(records_site.base)
    if case == "same_title":
        rows[1]["title"] = "Blue Notebook"
    else:
        rows[0]["title"] = "नमस्ते 日本語 café 🚀"
    assert result.outputs == {"products": rows}


def test_sensitive_zero_false_and_null_are_redacted_without_mutating_results(tmp_path):
    data = contract("https://example.com")
    data["permissions"]["allowed_domains"] = ["example.com"]
    for name in ["count", "amount", "available"]:
        data["outputs"]["products"]["items"]["properties"][name]["sensitive"] = True
    loaded = write_contract(tmp_path / "rows.yaml", data)
    values = {"products": expected("https://example.com")}
    redacted = redact_outputs(values, loaded.workflow, {})
    for row in redacted["products"]:
        for key in ["count", "amount", "available", "note"]:
            assert row[key] == "[REDACTED]"
    assert values == {"products": expected("https://example.com")}


def test_product_link_query_keeps_spaces_and_plus_characters(records_site, tmp_path):
    loaded = write_contract(tmp_path / "rows.yaml", contract(records_site.base))
    result = asyncio.run(replay_workflow(loaded, ["case=url_spaces"]))
    rows = expected(records_site.base)
    for row in rows:
        row["product_url"] = row["product_url"].replace(
            "?edition=paper", "?label=two%20%20spaces&tag=C++"
        )
    assert result.outputs == {"products": rows}


def test_whole_structured_output_can_be_hidden_in_trace(tmp_path):
    data = contract("https://example.com")
    data["permissions"]["allowed_domains"] = ["example.com"]
    data["outputs"]["products"]["sensitive"] = True
    loaded = write_contract(tmp_path / "rows.yaml", data)
    assert redact_outputs({"products": expected("https://example.com")}, loaded.workflow, {}) == {
        "products": "[REDACTED]"
    }


def test_unmarked_null_and_primitive_types_survive_trace_serialization(records_site, tmp_path):
    data = contract(records_site.base)
    data["outputs"]["products"]["items"]["properties"]["note"]["sensitive"] = False
    loaded = write_contract(tmp_path / "rows.yaml", data)
    result = asyncio.run(replay_workflow(loaded, ["case=normal"], tmp_path / "traces"))
    trace = json.loads(Path(result.trace_path).read_text())
    assert trace["outputs"] == result.outputs
    assert trace["outputs"]["products"][1]["note"] is None
    for row in trace["outputs"]["products"]:
        assert type(row["count"]) is int
        assert type(row["amount"]) is float
        assert type(row["available"]) is bool



def test_product_link_keeps_empty_query_delimiter(records_site, tmp_path):
    loaded = write_contract(tmp_path / "rows.yaml", contract(records_site.base))
    result = asyncio.run(replay_workflow(loaded, ["case=empty_query"]))
    rows = expected(records_site.base)
    for row in rows:
        row["product_url"] = row["product_url"].replace("?edition=paper", "?")
    assert result.outputs == {"products": rows}
