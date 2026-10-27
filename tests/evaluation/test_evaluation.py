"""Actual Chromium checks, input sentinels and stale/corrupt approval evidence."""

import asyncio
import copy
import json
import os
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
from evaluation_site import candidate, suite, write_candidate

from browser_to_mcp.cli import run
from browser_to_mcp.evaluation.models import Suite
from browser_to_mcp.evaluation.service import digest, evaluate, verify_evaluation


def setup(tmp_path, site):
    workflow = write_candidate(tmp_path / "draft.candidate", candidate(site.base))
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(suite(site.base)))
    return workflow, plan, digest(plan.read_bytes())


def invoke(tmp_path, site, *, change=None, plan_change=None):
    workflow, plan, pin = setup(tmp_path, site)
    if change:
        value = candidate(site.base)
        change(value)
        write_candidate(workflow, value)
    if plan_change:
        value = json.loads(plan.read_text())
        plan_change(value)
        plan.write_text(json.dumps(value))
        pin = digest(plan.read_bytes())
    directory = tmp_path / "attempt"
    report = asyncio.run(evaluate(workflow, plan, directory, pin))
    return report, (workflow, plan, directory, pin)


def test_exact_independent_results_types_paging_and_negatives(evaluation_site, tmp_path):
    report, args = invoke(tmp_path, evaluation_site)
    assert report.eligible, report.blockers
    assert len(report.observations) == 7
    assert all(o.status == "passed" for o in report.observations)
    assert report.observations[-1].outcome == "error"
    assert report.observations[-2].trace_file is None
    assert not any("q=True" in hit or "q=true" in hit for hit in evaluation_site.hits)
    assert report.source.source_sha256.startswith("sha256:")
    assert report.source.python and report.source.playwright
    assert report.generation is None and report.local_compute_cost_microusd is None
    assert report.replay_api_cost_microusd == 0
    before = list(evaluation_site.hits)
    assert verify_evaluation(*args) == report
    assert evaluation_site.hits == before  # Rechecking approval performs no browser work.
    text = (args[2] / "report.md").read_text()
    assert "changed_markup | passed | error" in text and "not a successful data result" in text
    trace = json.loads((args[2] / report.observations[0].trace_file).read_text())
    assert trace["metadata"]["browser_version"] != "unknown"
    assert trace["outputs"]["products"][1]["price_text"] == "£0.00"
    assert trace["outputs"]["collection"]["pages_read"] == 2
    destination = os.environ.get("BROWSER_TO_MCP_EVALUATION_EVIDENCE")
    if destination:
        # Retain the actual attempt and independent inputs for offline CI review.
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(args[2], target)
        shutil.copy2(args[0], target / "draft.candidate")
        shutil.copy2(args[1], target / "plan.json")


@pytest.mark.parametrize(
    "mutation",
    [
        "ignore_query",
        "ignore_color",
        "first_page_only",
        "wrong_price",
        "repeat_page",
        "wrong_expected_price",
        "wrong_expected_type",
    ],
)
def test_plausible_but_wrong_drafts_are_rejected(evaluation_site, tmp_path, mutation):
    def change(value):
        if mutation == "ignore_query":
            value["steps"][0]["url"] = value["steps"][0]["url"].replace("{{query}}", "notebook")
        if mutation == "ignore_color":
            value["steps"][0]["url"] = value["steps"][0]["url"].replace("{{color}}", "all")
        if mutation == "first_page_only":
            value["steps"][1]["paging"]["max_pages"] = 1

    def change_plan(value):
        if mutation == "wrong_expected_price":
            value["cases"][0]["expected"]["outputs"]["products"][0]["price_text"] = "£12.51"
        if mutation == "wrong_expected_type":
            value["cases"][0]["expected"]["outputs"]["collection"]["pages_read"] = 2.0

    if mutation == "wrong_price":
        evaluation_site.mode = "wrong_price"
    if mutation == "repeat_page":
        evaluation_site.mode = "repeat"
    if mutation == "wrong_expected_type":
        with pytest.raises(ValueError):
            invoke(tmp_path, evaluation_site, plan_change=change_plan)
        assert not evaluation_site.hits
        return
    report, args = invoke(tmp_path, evaluation_site, change=change, plan_change=change_plan)
    assert not report.eligible
    assert any(o.status == "failed" for o in report.observations)
    with pytest.raises(ValueError, match="Required checks"):
        verify_evaluation(*args)


@pytest.fixture
def passed_attempt(evaluation_site, tmp_path):
    return invoke(tmp_path, evaluation_site)


@pytest.mark.parametrize(
    "mutation",
    [
        "candidate",
        "comment",
        "suite",
        "hash",
        "missing_case",
        "missing_trace",
        "trace_bytes",
        "trace_inputs",
        "trace_digest",
        "trace_outputs",
        "forged_verdict",
        "trace_symlink",
    ],
)
def test_old_or_changed_evidence_cannot_approve_a_draft(passed_attempt, mutation, tmp_path):
    report, args = passed_attempt
    workflow, plan, directory, pin = args
    report_path = directory / "report.json"
    payload = json.loads(report_path.read_text())
    trace_path = directory / report.observations[0].trace_file
    if mutation in {"candidate", "comment"}:
        workflow.write_text(
            workflow.read_text()
            + ("\n# changed\n" if mutation == "comment" else "\ndescription: Changed description\n")
        )
    elif mutation == "suite":
        plan.write_text(plan.read_text() + "\n")
    elif mutation == "hash":
        args = (workflow, plan, directory, "sha256:" + "0" * 64)
    elif mutation == "missing_case":
        payload["observations"].pop()
    elif mutation == "missing_trace":
        trace_path.unlink()
    elif mutation == "trace_bytes":
        trace_path.write_text(trace_path.read_text() + "\n")
    elif mutation == "trace_symlink":
        real = tmp_path / "elsewhere.json"
        trace_path.rename(real)
        trace_path.symlink_to(real)
    elif mutation.startswith("trace_"):
        trace = json.loads(trace_path.read_text())
        if mutation == "trace_inputs":
            trace["inputs"]["query"] = "red"
        elif mutation == "trace_digest":
            trace["metadata"]["manifest_digest"] = "sha256:" + "0" * 64
        else:
            trace["outputs"]["products"][0]["price_text"] = "£0.01"
        trace_path.write_text(json.dumps(trace))
        payload["observations"][0]["trace_sha256"] = digest(trace_path.read_bytes())
    elif mutation == "forged_verdict":
        payload["eligible"] = False
    report_path.write_text(json.dumps(payload))
    with pytest.raises((ValueError, OSError)):
        verify_evaluation(*args)


@pytest.mark.parametrize("mode,expected_status", [("blocked", "blocked"), ("skip", "untested")])
def test_unavailable_required_cases_remain_visible(
    evaluation_site, tmp_path, mode, expected_status
):
    def change(value):
        value["cases"][1][mode + "_reason" if mode == "skip" else "blocked_reason"] = (
            "Model unavailable; no proposal was received"
            if mode == "blocked"
            else "Not yet measured"
        )

    report, args = invoke(tmp_path, evaluation_site, plan_change=change)
    assert not report.eligible and report.observations[1].status == expected_status
    assert report.observations[1].outcome == "none"
    assert len(report.observations) == 7
    with pytest.raises(ValueError):
        verify_evaluation(*args)


@pytest.mark.parametrize("search", ["outage", "challenge"])
def test_live_failures_are_not_correct_results(evaluation_site, tmp_path, search):
    def change(value):
        value["mode"] = "live"
        value["cases"][-1]["inputs"]["query"] = search
        value["cases"][-1]["expected"]["category"] = "runtime"
        value["cases"][-1]["expected"]["path"] = "steps.products"
        if search == "outage":
            value["cases"][-1]["expected"]["path"] = "steps.open"

    report, args = invoke(tmp_path, evaluation_site, plan_change=change)
    assert not report.eligible
    assert report.mode == "live" and report.observations[-1].status == "blocked"
    assert report.observations[-1].outcome == "error"
    assert (args[2] / report.observations[-1].trace_file).exists()


def test_no_silent_coverage_waivers_or_relabelled_examples(evaluation_site, tmp_path):
    def change(value):
        value["cases"] = value["cases"][:1]
        value["not_applicable"] = {"changed_inputs": "We only need the shown example"}

    report, _ = invoke(tmp_path, evaluation_site, plan_change=change)
    assert not report.eligible
    assert any("cannot be waived" in b for b in report.blockers)
    assert any("combined_filters" in b for b in report.blockers)


def test_same_input_with_a_different_label_does_not_cover_changed_inputs(evaluation_site, tmp_path):
    def change(value):
        value["cases"][1]["inputs"] = copy.deepcopy(value["cases"][0]["inputs"])
        value["cases"][1]["expected"] = copy.deepcopy(value["cases"][0]["expected"])

    report, _ = invoke(tmp_path, evaluation_site, plan_change=change)
    assert not report.eligible
    assert any("no different input and answer for query" in b for b in report.blockers)


def test_cli_evaluate_check_and_refuse_overwriting(evaluation_site, tmp_path, capsys):
    workflow, plan, pin = setup(tmp_path, evaluation_site)
    args = [
        str(workflow),
        str(plan),
        "--suite-sha256",
        pin,
        "--evidence-dir",
        str(tmp_path / "attempt"),
    ]
    assert run(["evaluate", *args]) == 0
    first = json.loads(capsys.readouterr().out)
    assert run(["check-evaluation", *args]) == 0
    assert json.loads(capsys.readouterr().out) == first
    files = {p: p.read_bytes() for p in (tmp_path / "attempt").rglob("*") if p.is_file()}
    hits = list(evaluation_site.hits)
    assert run(["evaluate", *args]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "error"
    assert evaluation_site.hits == hits and all(p.read_bytes() == data for p, data in files.items())


def test_bad_pin_never_starts_browser(evaluation_site, tmp_path, monkeypatch):
    workflow, plan, _ = setup(tmp_path, evaluation_site)
    browser = Mock(side_effect=AssertionError("No browser allowed"))
    monkeypatch.setattr("browser_to_mcp.replay.async_playwright", browser)
    with pytest.raises(ValueError, match="approved plan"):
        asyncio.run(evaluate(workflow, plan, tmp_path / "attempt", "sha256:" + "0" * 64))
    browser.assert_not_called()
    assert not (tmp_path / "attempt").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"cases": []},
        {"unknown": True},
        {"timeout_seconds": True},
        {"timeout_seconds": 121},
        {"mode": "ci_and_live"},
        {"wall_seconds": 3601},
    ],
)
def test_bad_plans_are_rejected(change):
    value = suite("http://127.0.0.1:1234") | change
    with pytest.raises(ValueError):
        Suite.model_validate(value)


def test_timeout_retains_completed_checks_and_all_untested_cases(evaluation_site, tmp_path):
    workflow, plan, pin = setup(tmp_path, evaluation_site)
    value = json.loads(plan.read_text())
    value["wall_seconds"] = 1
    plan.write_text(json.dumps(value))
    pin = digest(plan.read_bytes())
    directory = tmp_path / "interrupted"
    with pytest.raises(TimeoutError):
        asyncio.run(evaluate(workflow, plan, directory, pin))
    report = json.loads((directory / "report.json").read_text())
    assert not report["eligible"] and len(report["observations"]) == 7
    assert any(c["status"] == "untested" for c in report["observations"])
    with pytest.raises(ValueError):
        verify_evaluation(workflow, plan, directory, pin)


def test_forging_a_green_summary_does_not_fix_incorrect_outputs(evaluation_site, tmp_path):
    evaluation_site.mode = "wrong_price"
    report, args = invoke(tmp_path, evaluation_site)
    assert not report.eligible
    path = args[2] / "report.json"
    value = json.loads(path.read_text())
    for case in value["observations"]:
        case["status"] = "passed"
    value["blockers"], value["eligible"] = [], True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="actual outcome"):
        verify_evaluation(*args)


@pytest.mark.parametrize(
    "bad_json", [b'{"name":"one","name":"two"}', b'{"number":NaN}', b'{"number":1e999}']
)
def test_ambiguous_or_nonfinite_plans_are_rejected(tmp_path, bad_json):
    from browser_to_mcp.evaluation.service import read_json

    path = tmp_path / "bad.json"
    path.write_bytes(bad_json)
    with pytest.raises(ValueError):
        read_json(path)


def test_expected_boolean_is_not_an_integer():
    from browser_to_mcp.evaluation.service import _exact

    assert not _exact({"rows": [{"value": False}]}, {"rows": [{"value": 0}]})
    assert not _exact({"value": 1}, {"value": 1.0})
    assert not _exact(["blue", "red"], ["red", "blue"])
    assert not _exact({"title": "Blue"}, {"title": "Blue", "extra": "Red"})


def test_optional_failure_is_visible_and_does_not_replace_required_checks(
    evaluation_site, tmp_path
):
    def change(value):
        optional = copy.deepcopy(value["cases"][1])
        optional["id"], optional["required"] = "optional_example", False
        optional["expected"]["outputs"]["products"][0]["price_text"] = "£99.99"
        value["cases"].append(optional)

    report, args = invoke(tmp_path, evaluation_site, plan_change=change)
    assert report.eligible and report.observations[-1].status == "failed"
    assert verify_evaluation(*args) == report


@pytest.mark.parametrize(
    "inputs",
    [
        {},
        {"query": "notebook"},
        {"query": 1, "color": "all"},
        {"query": False, "color": "all"},
        {"query": [], "color": "all"},
        {"query": {}, "color": "all"},
        {"query": None, "color": "all"},
        {"query": "", "color": "all"},
        {"query": "notebook", "color": "all", "extra": "x"},
    ],
)
def test_strict_invalid_inputs_do_not_open_a_browser(
    evaluation_site, tmp_path, monkeypatch, inputs
):
    workflow, plan, _ = setup(tmp_path, evaluation_site)
    value = json.loads(plan.read_text())
    invalid = value["cases"][-2]
    invalid["inputs"] = inputs
    value["cases"] = [invalid]
    plan.write_text(json.dumps(value))
    browser = Mock(side_effect=AssertionError("Invalid input cannot start a browser"))
    monkeypatch.setattr("browser_to_mcp.replay.async_playwright", browser)
    report = asyncio.run(evaluate(workflow, plan, tmp_path / "attempt", digest(plan.read_bytes())))
    assert report.observations[0].status == "passed"
    assert report.observations[0].outcome == "error"
    assert not report.eligible  # A single negative case cannot stand in for the whole plan.
    assert not evaluation_site.hits
    browser.assert_not_called()
