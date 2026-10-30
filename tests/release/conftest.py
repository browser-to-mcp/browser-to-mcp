import json
import math
import os
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from browser_to_mcp.evaluation.service import _source_version

sys.path.insert(0, str(Path(__file__).parents[1] / "builder"))


@pytest.fixture(autouse=True)
def no_live_provider(monkeypatch):
    for name in os.environ:
        if name.startswith(("BROWSER_TO_MCP_MODEL_", "BROWSER_TO_MCP_OPENAI_")) or name in {
            "BROWSER_TO_MCP_PROVIDER",
            "OPENAI_API_KEY",
        }:
            monkeypatch.delenv(name)


@pytest.fixture(scope="session")
def release_report(tmp_path_factory):
    destination = os.environ.get("BROWSER_TO_MCP_RELEASE_REPORT")
    path = Path(destination) if destination else tmp_path_factory.mktemp("release") / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        pytest.fail("Choose a new report path; keep earlier attempts for review.")
    report = {
        "kind": "controlled three-layout creation-to-published-MCP journey",
        "started_at": datetime.now(UTC).isoformat(),
        "source": _source_version().model_dump(mode="json"),
        "proposal_source": "fixed protocol replies; no actual model inference",
        "client": "Python MCP SDK over STDIO; not a native assistant client",
        "installation_seconds": None,
        "manual_baseline_seconds": None,
        "newcomer_seconds": None,
        "repeat_users": None,
        "external_client_tokens": None,
        "external_client_cost_usd": None,
        "local_compute_cost_usd": None,
        "live_trials": "separate opt-in evidence; not part of this controlled result",
        "builds": [],
        "runs": [],
        "refusals": [],
        "cleanup": [],
        "journeys": [],
    }
    try:
        yield report, path
    finally:
        builds, runs = report["builds"], report["runs"]
        seconds = sorted(r["seconds"] for r in runs)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["gates"] = {
            "controlled_creation": len(builds) == 9 and sum(b["matched"] for b in builds) >= 8,
            "controlled_first_result": len(builds) == 9
            and sum(b["matched"] and b["seconds"] <= 600 for b in builds) >= 8,
            "controlled_correctness": len(runs) == 90 and all(r["matched"] for r in runs),
            "controlled_latency": len(runs) == 90
            and seconds[math.ceil(0.95 * len(seconds)) - 1] <= 10,
            "complete_journeys": len(report["journeys"]) == 3
            and all(j["matched"] for j in report["journeys"]),
            "model_cost": "unmeasured; no inference performed",
            "live_creation": "pending real-model trials (#62)",
            "human_baseline_and_newcomer": "pending (#48)",
            "repeat_use": "pending real users (#57/#66)",
            "release": "pending; controlled checks alone do not complete #66",
        }
        report["latency_seconds"] = {
            "median": statistics.median(seconds) if seconds else None,
            "p95_nearest_rank": seconds[math.ceil(0.95 * len(seconds)) - 1] if seconds else None,
        }
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        failed = [name for name, passed in report["gates"].items() if passed is False]
        if failed:
            pytest.fail("Controlled release gates failed: " + ", ".join(failed))
