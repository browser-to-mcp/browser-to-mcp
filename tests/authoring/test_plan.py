"""Review and export boundaries, including exact input binding and rejected drafts."""

import json

import pytest

from browser_to_mcp.authoring.export import export_workflow
from browser_to_mcp.authoring.plan import (
    AuthoringError,
    bind_input,
    build_workflow,
    input_definition,
    read_draft,
    recorded_steps,
    step_fields,
)
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.recorder.draft import Navigation, RecordingDraft
from browser_to_mcp.validation import WorkflowValidationError


def plan():
    return build_workflow(
        "lookup",
        "Find a book",
        ["example.com"],
        [
            step_fields("goto", "start", url="https://example.com/{{slug}}"),
            step_fields("extract_text", "title", selector="h1"),
        ],
        {"slug": input_definition("string", True, "Book slug", 1)},
        {"title": {"type": "string"}},
    )


def test_binding_is_local_and_preserves_fixed_url_encoding():
    source = step_fields("goto", "start", url="https://example.com/book/book?q=book&x=book%20one")
    bound = bind_input(source, "path:1", "slug")
    assert bound["url"] == "https://example.com/{{slug}}/book?q=book&x=book%20one"
    assert source["url"].startswith("https://example.com/book/book")
    bound = bind_input(source, "query:0", "query")
    assert bound["url"] == "https://example.com/book/book?q={{query}}&x=book%20one"
    fill = {"action": "fill", "value": "book", "selector": '[name="book"]'}
    assert bind_input(fill, "value", "title")["selector"] == fill["selector"]


@pytest.mark.parametrize("name", ["bad name", "class", "model_dump", "_hidden", "x-y"])
def test_invalid_input_names(name):
    with pytest.raises(AuthoringError, match="inputs"):
        bind_input({"action": "fill", "value": "x"}, "value", name)


def test_export_requires_review_and_never_overwrites(tmp_path):
    destination = tmp_path / "lookup.yaml"
    with pytest.raises(AuthoringError, match="review"):
        export_workflow(plan(), destination, reviewed=False)
    assert not destination.exists()
    export_workflow(plan(), destination, reviewed=True)
    assert load_workflow(destination).manifest_digest == plan().manifest_digest
    content = destination.read_bytes()
    with pytest.raises(AuthoringError, match="exists"):
        export_workflow(plan(), destination, reviewed=True)
    assert destination.read_bytes() == content
    assert not list(tmp_path.glob(".review-*"))


@pytest.mark.parametrize("changes", [{"status": "partial"}, {"version": 99}, {"events": []}])
def test_incomplete_and_unsupported_drafts(tmp_path, changes):
    data = RecordingDraft(
        status="captured",
        allowed_domains=["example.com"],
        events=[Navigation(url="https://example.com")],
    ).model_dump()
    data.update(changes)
    path = tmp_path / "draft.recording.json"
    path.write_text(json.dumps(data))
    with pytest.raises(AuthoringError):
        read_draft(path)


def test_navigation_observations_are_explicit():
    draft = RecordingDraft(
        status="captured",
        allowed_domains=["example.com"],
        events=[Navigation(url="https://example.com"), Navigation(url="https://example.com/next")],
    )
    assert len(recorded_steps(draft, set())) == 2
    assert len(recorded_steps(draft, {1})) == 1
    with pytest.raises(AuthoringError):
        recorded_steps(draft, {0})


def test_normal_policy_rejects_undeclared_hosts_and_retries():
    loaded = plan()
    raw = loaded.workflow.model_dump(mode="json")
    for mutate in (
        lambda steps: steps[0].update(url="https://elsewhere.com/"),
        lambda steps: steps[0].update(retry="one"),
    ):
        steps = [dict(step) for step in raw["steps"]]
        mutate(steps)
        with pytest.raises(WorkflowValidationError):
            build_workflow(
                raw["name"],
                raw["description"],
                ["example.com"],
                steps,
                raw["inputs"],
                raw["outputs"],
            )


def test_missing_and_optional_bound_inputs_fail_before_export():
    raw = plan().workflow.model_dump(mode="json")
    for inputs in ({}, {"slug": {"type": "string", "required": False}}):
        with pytest.raises(AuthoringError, match="input"):
            build_workflow(
                raw["name"],
                raw["description"],
                ["example.com"],
                raw["steps"],
                inputs,
                raw["outputs"],
            )


@pytest.mark.parametrize("kind", ["string", "integer", "number", "boolean"])
def test_primitive_input_definitions_have_no_sample_default(kind):
    definition = input_definition(kind, True, "A value", None)
    assert definition["type"] == kind
    assert "default" not in definition


def test_atomic_export_loses_race_without_replacing_winner(tmp_path, monkeypatch):
    import os

    destination = tmp_path / "lookup.yaml"
    real_link = os.link

    def racing_link(source, target):
        destination.write_text("another author's workflow")
        return real_link(source, target)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(AuthoringError, match="Could not save"):
        export_workflow(plan(), destination, reviewed=True)
    assert destination.read_text() == "another author's workflow"
    assert not list(tmp_path.glob(".review-*"))


def test_typed_preview_preserves_zero_and_false():
    from browser_to_mcp.replay import WorkflowExecutionError, process_outputs

    loaded = build_workflow(
        "stock",
        "Read stock",
        ["example.com"],
        [
            step_fields("goto", "start", url="https://example.com"),
            step_fields("extract_text", "count", selector="#count"),
            step_fields("extract_text", "available", selector="#available"),
            step_fields("extract_text", "amount", selector="#amount"),
        ],
        {},
        {
            "count": {"type": "integer"},
            "available": {"type": "boolean"},
            "amount": {"type": "number"},
        },
    )
    assert process_outputs(
        loaded.workflow, {"count": "0", "available": "false", "amount": "1.5"}
    ) == {
        "count": 0,
        "available": False,
        "amount": 1.5,
    }
    with pytest.raises(WorkflowExecutionError):
        process_outputs(loaded.workflow, {"count": "0", "available": "false", "amount": "NaN"})


def test_terminal_prompt_preserves_case_and_cancels_on_eof(monkeypatch):
    import asyncio
    import io
    import sys

    from browser_to_mcp.authoring.command import Cancelled, terminal_prompt

    monkeypatch.setattr(sys, "stdin", io.StringIO("A Book Title\n"))
    assert asyncio.run(terminal_prompt("Description")) == "A Book Title"
    with pytest.raises(Cancelled):
        asyncio.run(terminal_prompt("Description"))


def test_author_refuses_draft_in_workflow_directory_before_browser(tmp_path):
    import asyncio

    from browser_to_mcp.authoring.command import author

    with pytest.raises(AuthoringError, match="outside"):
        asyncio.run(author(tmp_path / "draft.recording.json", tmp_path / "tool.yaml"))
    assert not (tmp_path / "tool.yaml").exists()


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("click", {}),
        ("fill", {"value": "public book"}),
        ("press", {"key": "Enter"}),
    ],
)
def test_nested_recorded_targets_are_not_mistaken_for_templates(tmp_path, kind, fields):
    data = {
        "format": "browser-to-mcp-recording",
        "version": 1,
        "status": "captured",
        "requires_review": True,
        "allowed_domains": ["example.com"],
        "events": [
            {"kind": "navigation", "url": "https://example.com"},
            {"kind": kind, "target": {"selector": "#book", "strategy": "id"}, **fields},
        ],
    }
    path = tmp_path / "book.recording.json"
    path.write_text(json.dumps(data))
    assert read_draft(path).events[1].kind == kind
    data["events"][1]["target"]["selector"] = "#book}}"
    path.write_text(json.dumps(data))
    with pytest.raises(AuthoringError, match="templates"):
        read_draft(path)
