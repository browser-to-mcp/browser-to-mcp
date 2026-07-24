from __future__ import annotations

import json
from pathlib import Path

from pytest import CaptureFixture

from browser_to_mcp.cli import run
from browser_to_mcp.replay import ActionResult, ActionStatus, ReplayResult

WORKFLOW_PATH = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


def test_validate_command_reports_a_valid_workflow(capsys: CaptureFixture[str]) -> None:
    assert run(["validate", str(WORKFLOW_PATH)]) == 0

    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "valid"
    assert result["workflow"] == "hn_search"


def test_validate_command_reports_invalid_yaml(tmp_path: Path, capsys: CaptureFixture[str]) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("name: [", encoding="utf-8")

    assert run(["validate", str(invalid)]) == 2

    output = capsys.readouterr().err
    result = json.loads(output)
    assert result["status"] == "invalid"
    assert result["category"] == "schema"


def test_dry_run_reports_a_serializable_replay_plan(capsys: CaptureFixture[str]) -> None:
    assert run(["dry-run", str(WORKFLOW_PATH)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "planned"
    assert result["workflow"] == "hn_search"
    assert result["inputs"]["query"]["type"] == "string"
    assert result["steps"] == [
        {"id": "search", "action": "goto", "timeout_seconds": 30},
        {"id": "results", "action": "extract_text", "timeout_seconds": 30},
    ]


def test_replay_rejects_missing_input_before_browser_start(capsys: CaptureFixture[str]) -> None:
    assert run(["replay", str(WORKFLOW_PATH)]) == 2

    result = json.loads(capsys.readouterr().err)
    assert result == {
        "status": "failed",
        "category": "input",
        "path": "inputs.query",
        "message": "A required input was not provided.",
    }


def test_replay_command_reports_structured_action_results(
    monkeypatch, capsys: CaptureFixture[str]
) -> None:
    async def fake_replay(*args, **kwargs) -> ReplayResult:
        return ReplayResult(
            workflow="hn_search",
            manifest_digest="sha256:test",
            outputs={"results": "visible results"},
            actions=(
                ActionResult(
                    step_id="search",
                    action="goto",
                    status=ActionStatus.COMPLETED,
                ),
            ),
            duration_seconds=0.01,
        )

    monkeypatch.setattr("browser_to_mcp.cli.replay_workflow", fake_replay)

    assert run(["replay", str(WORKFLOW_PATH), "--input", "query=mcp"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["actions"] == [
        {
            "step_id": "search",
            "action": "goto",
            "status": "completed",
            "output_key": None,
        }
    ]
