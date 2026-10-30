"""Exercise the actual report teardown, including failed-report retention."""

import json
import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "controlled_first_result",
        "controlled_latency",
        "controlled_correctness",
        "controlled_creation",
        "complete_journeys",
    ],
)
def test_report_enforces_gates_after_saving_evidence(
    tmp_path, tmp_path_factory, monkeypatch, failure
):
    path = tmp_path / "report.json"
    monkeypatch.setenv("BROWSER_TO_MCP_RELEASE_REPORT", str(path))
    fixture = runpy.run_path(str(Path(__file__).with_name("conftest.py")))["release_report"]
    teardown = fixture.__wrapped__(tmp_path_factory)
    report, _ = next(teardown)
    report["builds"] = [{"matched": True, "seconds": 600} for _ in range(9)]
    report["runs"] = [{"matched": True, "seconds": 10} for _ in range(90)]
    report["journeys"] = [{"matched": True} for _ in range(3)]
    if failure == "controlled_first_result":
        for build in report["builds"][:2]:
            build["seconds"] = 600.01
    elif failure == "controlled_latency":
        for run in report["runs"][:5]:
            run["seconds"] = 10.01
    elif failure == "controlled_correctness":
        report["runs"][0]["matched"] = False
    elif failure == "controlled_creation":
        for build in report["builds"][:2]:
            build["matched"] = False
    elif failure == "complete_journeys":
        report["journeys"][0]["matched"] = False
    if failure:
        with pytest.raises(pytest.fail.Exception, match=failure):
            next(teardown)
    else:
        with pytest.raises(StopIteration):
            next(teardown)
    saved = json.loads(path.read_text())
    assert (
        saved["gates"][failure] is False
        if failure
        else all(value is True for value in saved["gates"].values() if isinstance(value, bool))
    )
    assert saved["runs"] == report["runs"]
    assert saved["gates"]["release"].startswith("pending")
