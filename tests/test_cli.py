from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
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


@pytest.mark.parametrize("command", ["validate", "dry-run", "replay"])
@pytest.mark.parametrize("retry", ["one", "bounded"])
def test_commands_refuse_unsupported_retries_before_browser_start(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch, command: str, retry: str
) -> None:
    invalid = tmp_path / "retry.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace(
            "    retry: none", f"    retry: {retry}", 1
        ),
        encoding="utf-8",
    )
    browser_start = Mock(side_effect=AssertionError("Policy refusal must not start Playwright."))
    monkeypatch.setattr("browser_to_mcp.replay.async_playwright", browser_start)
    arguments = [command, str(invalid)]
    if command == "replay":
        arguments.extend(["--input", "query=mcp", "--trace-dir", str(tmp_path / "traces")])

    assert run(arguments) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "invalid",
        "category": "policy",
        "path": "steps.search.retry",
        "message": "V0 does not support retries. Set retry to none.",
    }
    browser_start.assert_not_called()
    assert not (tmp_path / "traces").exists()


def test_dry_run_reports_a_serializable_replay_plan(capsys: CaptureFixture[str]) -> None:
    assert run(["dry-run", str(WORKFLOW_PATH)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "planned"
    assert result["workflow"] == "hn_search"
    assert result["inputs"]["query"]["type"] == "string"
    assert result["steps"] == [
        {
            "id": "search",
            "action": "goto",
            "timeout_seconds": 30,
            "risk": "read_only",
            "idempotency": "safe",
            "retry": "none",
            "confirmation": "never",
        },
        {
            "id": "wait_for_results",
            "action": "wait_for_text",
            "timeout_seconds": 30,
            "risk": "read_only",
            "idempotency": "safe",
            "retry": "none",
            "confirmation": "never",
        },
        {
            "id": "results",
            "action": "extract_text",
            "timeout_seconds": 30,
            "risk": "read_only",
            "idempotency": "safe",
            "retry": "none",
            "confirmation": "never",
        },
    ]


def test_replay_rejects_missing_input_before_browser_start(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    assert run(["replay", str(WORKFLOW_PATH), "--trace-dir", str(tmp_path)]) == 2

    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "failed"
    assert result["category"] == "input"
    assert result["path"] == "inputs.query"
    assert result["message"] == "A required input was not provided."
    assert Path(result["trace_path"]).exists()


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
            trace_path="traces/trace.json",
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
    assert result["trace_path"] == "traces/trace.json"


def test_replay_command_forwards_overall_timeout(
    monkeypatch, capsys: CaptureFixture[str]
) -> None:
    received: dict[str, object] = {}

    async def fake_replay(*args, **kwargs) -> ReplayResult:
        received["timeout_seconds"] = args[3]
        return ReplayResult(
            workflow="hn_search",
            manifest_digest="sha256:test",
            outputs={"results": "visible results"},
            actions=(),
            duration_seconds=0.01,
        )

    monkeypatch.setattr("browser_to_mcp.cli.replay_workflow", fake_replay)

    assert (
        run(
            [
                "replay",
                str(WORKFLOW_PATH),
                "--input",
                "query=mcp",
                "--timeout-seconds",
                "12",
            ]
        )
        == 0
    )

    assert received["timeout_seconds"] == 12
    assert json.loads(capsys.readouterr().out)["status"] == "completed"


def test_server_command_reports_invalid_workflow_directory(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing-workflows"

    assert run(["server", "--workflow-dir", str(missing)]) == 2

    result = json.loads(capsys.readouterr().err)
    assert result == {
        "status": "invalid",
        "category": "configuration",
        "path": str(missing),
        "message": "Workflow directory does not exist.",
    }
