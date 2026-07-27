from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from browser_to_mcp.readiness import ReadinessCategory, readiness_fixtures, run_readiness


def test_fixture_matrix_covers_every_required_m3_scenario() -> None:
    fixtures = {fixture.name: fixture for fixture in readiness_fixtures()}

    assert set(fixtures) == {
        "changed_selector",
        "delayed_page",
        "malformed_output",
        "unauthorized_domain",
        "hostile_page_content",
    }
    assert fixtures["changed_selector"].expected_category is (
        ReadinessCategory.INFRASTRUCTURE_FAILURE
    )
    assert fixtures["delayed_page"].expected_category is ReadinessCategory.TIMEOUT
    assert fixtures["malformed_output"].expected_category is ReadinessCategory.REFUSAL
    assert fixtures["unauthorized_domain"].expected_category is ReadinessCategory.REFUSAL
    assert fixtures["hostile_page_content"].expected_category is ReadinessCategory.SUCCESS


def test_repeated_fixture_runs_have_stable_expected_classifications() -> None:
    report = run_readiness(runs_per_fixture=4)

    assert report.ready is True
    assert len(report.fixtures) == 5
    for fixture in report.fixtures:
        assert fixture.success_rate == 1.0
        assert fixture.classifications == {fixture.expected_category: 4}
        assert fixture.min_latency_ms >= 0
        assert fixture.mean_latency_ms >= fixture.min_latency_ms
        assert fixture.max_latency_ms >= fixture.mean_latency_ms


def test_readiness_report_is_json_serializable() -> None:
    report = run_readiness(runs_per_fixture=1)

    payload = json.loads(json.dumps(report.to_dict()))

    assert payload["status"] == "ready"
    assert payload["runs_per_fixture"] == 1
    assert {fixture["name"] for fixture in payload["fixtures"]} == {
        "changed_selector",
        "delayed_page",
        "malformed_output",
        "unauthorized_domain",
        "hostile_page_content",
    }


def test_readiness_command_reports_the_same_repeatable_gate() -> None:
    repository_root = Path(__file__).parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/readiness_report.py", "--runs", "2"],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ready"
    assert payload["runs_per_fixture"] == 2


@pytest.mark.parametrize("runs", [0, -1])
def test_readiness_rejects_non_positive_run_counts(runs: int) -> None:
    with pytest.raises(ValueError, match="runs_per_fixture must be positive"):
        run_readiness(runs)
