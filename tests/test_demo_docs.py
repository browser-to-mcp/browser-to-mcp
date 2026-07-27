from __future__ import annotations

import json
import re
from pathlib import Path

from browser_to_mcp.cli import build_parser

ROOT = Path(__file__).parents[1]
DEMO_PATH = ROOT / "docs" / "DEMO.md"
WORKFLOW_PATH = ROOT / "workflows" / "hn_search.yaml"


def test_demo_documents_every_supported_local_onboarding_command() -> None:
    document = DEMO_PATH.read_text(encoding="utf-8")

    for command in (
        "uv python install 3.11",
        "uv sync --locked --all-groups",
        "uv run playwright install chromium",
        "uv run browser-to-mcp validate workflows/hn_search.yaml",
        "uv run browser-to-mcp dry-run workflows/hn_search.yaml",
        "uv run browser-to-mcp replay workflows/hn_search.yaml",
        "uv run browser-to-mcp server --workflow-dir workflows",
        "uv run python examples/mcp_demo.py --query python --trace-dir traces",
    ):
        assert command in document


def test_demo_mcp_configuration_is_valid_and_matches_the_server_cli() -> None:
    document = DEMO_PATH.read_text(encoding="utf-8")
    [configuration] = re.findall(r"```json\n(.*?)\n```", document, flags=re.DOTALL)

    server = json.loads(configuration)["mcpServers"]["browser-to-mcp"]

    assert server == {
        "command": "uv",
        "args": [
            "run",
            "browser-to-mcp",
            "server",
            "--workflow-dir",
            "workflows",
        ],
        "cwd": "/absolute/path/to/browser-to-mcp",
    }
    namespace = build_parser().parse_args(["server", "--workflow-dir", "workflows"])
    assert namespace.workflow_dir == Path("workflows")


def test_demo_controlled_failure_matches_current_input_validation() -> None:
    document = DEMO_PATH.read_text(encoding="utf-8")

    assert "--input query=" in document
    assert '"category": "input"' in document
    assert '"path": "inputs.query"' in document
    assert WORKFLOW_PATH.exists()
