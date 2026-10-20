from __future__ import annotations

import json
from pathlib import Path

import pytest

from browser_to_mcp.loader import load_workflow
from browser_to_mcp.recorder.capture import Capture
from browser_to_mcp.recorder.draft import (
    RecordingDraft,
    RecordingError,
    save_draft,
    validate_destination,
    validate_start,
)
from browser_to_mcp.server import load_workflows
from browser_to_mcp.validation import WorkflowValidationError

TARGET = {"selector": '[id="search"]', "strategy": "id"}


def test_typing_coalesces_without_removing_deliberate_actions():
    capture = Capture(["example.com"])
    for value in ["b", "bo", "book"]:
        capture.accept({"kind": "fill", "target": TARGET, "value": value})
    for _ in range(2):
        capture.accept({"kind": "click", "target": TARGET})
    capture.accept({"kind": "fill", "target": TARGET, "value": "second"})
    capture.accept({"kind": "boundary"})
    capture.accept({"kind": "fill", "target": TARGET, "value": ""})
    assert [event.kind for event in capture.events] == ["fill", "click", "click", "fill", "fill"]
    assert capture.events[0].value == "book"
    assert capture.events[-1].value == ""


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "diagnostic", "code": "sensitive_field", "value": "PASSWORD-SECRET"},
        {"kind": "fill", "target": TARGET, "value": "Bearer PASSWORD-SECRET"},
        {"kind": "navigation", "url": "https://example.com/?token=PASSWORD-SECRET"},
    ],
)
def test_sensitive_capture_halts_without_persisting_values(payload):
    capture = Capture(["example.com"])
    capture.accept(payload)
    capture.accept({"kind": "fill", "target": TARGET, "value": "PASSWORD-SECRET"})
    draft = capture.draft()
    assert capture.halted
    assert draft.status == "partial"
    assert "PASSWORD-SECRET" not in draft.model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"kind": "unknown"},
        {"kind": "diagnostic", "code": "SECRET-EXCEPTION"},
        {"kind": "fill", "target": TARGET, "value": 1},
        {"kind": "click", "target": {"selector": "x", "strategy": "unsupported"}},
    ],
)
def test_malformed_events_have_safe_diagnostics(payload):
    capture = Capture(["example.com"])
    capture.accept(payload)
    assert capture.draft().status == "partial"
    assert capture.events[0].code == "capture_error"
    assert "SECRET-EXCEPTION" not in capture.draft().model_dump_json()


@pytest.mark.parametrize(
    "url,domains",
    [
        ("https://example.com/", []),
        ("https://other.com/", ["example.com"]),
        ("https://example.com.evil.org/", ["example.com"]),
        ("file:///tmp/public", ["example.com"]),
        ("https://user:password@example.com/", ["example.com"]),
        ("https://example.com/?access_token=secret", ["example.com"]),
        ("https://example.com/#secret", ["example.com"]),
        ("https://example.com/", ["*.example.com"]),
        ("https://example.com/", ["https://example.com"]),
    ],
)
def test_invalid_start_refused_without_echoing_url(url, domains):
    with pytest.raises(RecordingError) as caught:
        validate_start(url, domains)
    assert url not in str(caught.value)


def test_explicit_domain_normalization_and_public_query():
    assert validate_start("https://shop.example.com/?q=books", ["EXAMPLE.COM.", "example.com"]) == [
        "example.com"
    ]


@pytest.mark.parametrize("name", ["draft.yaml", "draft.json", "draft.recording.json.yaml"])
def test_draft_requires_distinct_extension(tmp_path, name):
    with pytest.raises(RecordingError, match="recording.json"):
        validate_destination(tmp_path / name)


def test_draft_excludes_server_directory_and_symlink_alias(tmp_path):
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(workflows, target_is_directory=True)
    for path in [workflows / "draft.recording.json", alias / "draft.recording.json"]:
        with pytest.raises(RecordingError, match="outside"):
            validate_destination(path, workflows)


def test_save_roundtrip_is_private_exclusive_and_not_a_tool(tmp_path):
    draft = Capture(["example.com"]).draft()
    path = save_draft(draft, tmp_path / "draft.recording.json")
    assert RecordingDraft.model_validate_json(path.read_text()) == draft
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RecordingError, match="already exists"):
        save_draft(draft, path)
    with pytest.raises(WorkflowValidationError):
        load_workflow(path)
    # Even accidentally moving a draft next to a workflow cannot register it.
    source = Path(__file__).parents[2] / "workflows" / "hn_search.yaml"
    (tmp_path / "hn.yaml").write_text(source.read_text())
    assert [item.workflow.name for item in load_workflows(tmp_path)] == ["hn_search"]


def test_save_failure_removes_temporary_file_without_overwriting(tmp_path, monkeypatch):
    destination = tmp_path / "draft.recording.json"

    def racing_link(*args):
        destination.write_text("existing")
        raise FileExistsError("sensitive error")

    monkeypatch.setattr("browser_to_mcp.recorder.draft.os.link", racing_link)
    with pytest.raises(RecordingError, match="no existing file"):
        save_draft(Capture(["example.com"]).draft(), destination)
    assert destination.read_text() == "existing"
    assert list(tmp_path.iterdir()) == [destination]


def test_draft_version_and_format_are_explicit():
    data = json.loads(Capture(["example.com"]).draft().model_dump_json())
    assert data["version"] == 1
    assert data["format"] == "browser-to-mcp-recording"
    assert data["requires_review"] is True
