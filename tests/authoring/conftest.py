"""Local browser fixtures and optional machine-readable journey evidence."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from catalog import serve_catalog


@pytest.fixture
def catalog_server():
    with serve_catalog() as server:
        yield server


@pytest.fixture
def catalog(catalog_server):
    return catalog_server.base_url


@pytest.fixture
def journey_report():
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).parents[2],
    ).stdout.strip()
    report = {
        "kind": "controlled Chromium recording-to-MCP test",
        "observer": "automated software using real Chromium and a real MCP server",
        "prerequisites": "already installed; installation time not measured",
        "interaction": "scripted terminal answers and actual browser clicks",
        "newcomer_timing": "not measured",
        "working_tree_dirty": bool(dirty),
        "started_at": datetime.now(UTC).isoformat(),
        "commit": os.environ.get("GITHUB_SHA") or revision,
        "runs": [],
        "completed": False,
    }
    try:
        yield report
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["attempts"] = len(report["runs"])
        report["matching_outcomes"] = sum(run["matched"] for run in report["runs"])
        destination = os.environ.get("BROWSER_TO_MCP_JOURNEY_REPORT")
        if destination:
            path = Path(destination)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
