from __future__ import annotations

import json
from pathlib import Path

from browser_to_mcp.cli import run

WORKFLOW_PATH = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


def test_validate_command_reports_a_valid_workflow(capsys: object) -> None:
    assert run(["validate", str(WORKFLOW_PATH)]) == 0

    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["status"] == "valid"
    assert result["workflow"] == "hn_search"


def test_validate_command_reports_invalid_yaml(tmp_path: Path, capsys: object) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("name: [", encoding="utf-8")

    assert run(["validate", str(invalid)]) == 2

    output = capsys.readouterr().err
    result = json.loads(output)
    assert result["status"] == "invalid"
    assert result["category"] == "schema"
