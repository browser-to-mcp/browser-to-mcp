"""Synthetic failure pages stay offline and bound to the original live candidate."""

import asyncio
import copy
import json

import pytest
from builder_site import expected, proposal, serve, task

from browser_to_mcp.builder.contracts import TaskRequest, make_candidate
from browser_to_mcp.evaluation.models import Suite
from browser_to_mcp.evaluation.service import digest, evaluate, verify_evaluation
from browser_to_mcp.jobs.candidate import check_candidate
from browser_to_mcp.library.store import Library
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow

EMPTY = '<main id="catalog"></main>'
BROKEN = (
    '<main id="catalog"><article><a class="title" title="Book" '
    'href="/products/book">Book</a><i>£1</i></article></main>'
)


def prepare(root, base):
    request = task(base, max_pages=1, allow_empty=False, unique_by=None)
    request["timeout_seconds"] = 2
    _, data = make_candidate(
        TaskRequest.model_validate(request),
        json.dumps(proposal(paged=False, empty=False, unique=False)),
    )
    candidate = root / "draft.candidate"
    candidate.write_bytes(data)
    plan = dict(
        name="Mixed evidence",
        mode="mixed",
        requirements_source="Independent fixture spec",
        expectations_source="Fixed notebook rows",
        not_applicable={"combined_filters": "One input", "multiple_pages": "Single page"},
        cases=[
            dict(
                id="demo",
                covers=["demonstration"],
                inputs={"query": "notebook"},
                expected=dict(outcome="outputs", outputs={"result": expected(base, (0, 1))}),
            ),
            dict(
                id="changed",
                covers=["changed_inputs"],
                inputs={"query": "red"},
                expected=dict(outcome="outputs", outputs={"result": expected(base, (2,))}),
            ),
            dict(
                id="invalid",
                covers=["invalid_input"],
                inputs={},
                expected=dict(outcome="error", category="input", path="inputs"),
            ),
            dict(
                id="empty",
                covers=["zero_results"],
                inputs={"query": "notebook"},
                fixture_html=EMPTY,
                expected=dict(outcome="error", category="runtime", path="steps.result"),
            ),
            dict(
                id="broken",
                covers=["changed_page"],
                inputs={"query": "notebook"},
                fixture_html=BROKEN,
                expected=dict(
                    outcome="error", category="assertion", path="outputs.result.0.price_text"
                ),
            ),
        ],
    )
    return candidate, plan


def test_mixed_evidence_publishes_same_candidate_and_verifies_fixture_identity(tmp_path):
    with serve() as (site, base):
        candidate, plan = prepare(tmp_path, base)
        path = tmp_path / "plan.json"
        path.write_text(json.dumps(plan))
        pin = digest(path.read_bytes())
        directory = tmp_path / "evidence"
        report = asyncio.run(evaluate(candidate, path, directory, pin))
        assert report.eligible, report.blockers
        assert len(site.hits) == 2  # Only the two normal cases contacted the server.
        assert verify_evaluation(candidate, path, directory, pin) == report
        library = Library(tmp_path / "library")
        review = library.import_draft(candidate, origin="test")
        review = library.attach_tests(review["tool"]["id"], 1, directory, pin)
        library.activate(
            review["tool"]["id"],
            1,
            revision=review["tool"]["revision"],
            candidate_sha256=review["version"]["sha"],
            approve=True,
        )
        assert (
            library.published()[0].manifest_digest
            == check_candidate(candidate.read_text())[0].manifest_digest
        )
        observation = report.observations[-1]
        trace_path = directory / observation.trace_file
        trace = json.loads(trace_path.read_text())
        assert trace["metadata"]["evaluation_fixture_sha256"] == digest(BROKEN.encode())
        trace["metadata"]["evaluation_fixture_sha256"] = None
        trace_path.write_text(json.dumps(trace))
        report.observations[-1].trace_sha256 = digest(trace_path.read_bytes())
        (directory / "report.json").write_text(report.model_dump_json())
        with pytest.raises(ValueError, match="trace identity"):
            verify_evaluation(candidate, path, directory, pin)


@pytest.mark.parametrize(
    "markup",
    [
        '<main id="catalog"><img src="{base}/leak"></main>',
        '<main id="catalog"><iframe src="{base}/leak"></iframe></main>',
    ],
)
def test_fixture_requests_cannot_escape_to_network(tmp_path, markup):
    with serve() as (site, base):
        candidate, _ = prepare(tmp_path, base)
        loaded, _ = check_candidate(candidate.read_text())
        with pytest.raises(WorkflowExecutionError) as error:
            asyncio.run(
                replay_workflow(
                    loaded,
                    ["query=notebook"],
                    tmp_path / "traces",
                    evaluation_html=markup.format(base=base),
                )
            )
        assert error.value.category == "permission"
        assert site.hits == []


def test_fixture_scripts_are_disabled(tmp_path):
    with serve() as (site, base):
        candidate, _ = prepare(tmp_path, base)
        loaded, _ = check_candidate(candidate.read_text())
        html = (
            EMPTY + '<script>fetch("' + base + '/leak");document.body.innerHTML="changed";</script>'
        )
        with pytest.raises(WorkflowExecutionError) as error:
            asyncio.run(
                replay_workflow(
                    loaded, ["query=notebook"], tmp_path / "traces", evaluation_html=html
                )
            )
        assert error.value.category == "runtime" and error.value.path == "steps.result"
        assert site.hits == []


@pytest.mark.parametrize("mutation", ["live", "controlled", "all_fixture", "success_fixture"])
def test_fixture_evidence_cannot_be_labelled_as_live_success(tmp_path, mutation):
    _, plan = prepare(tmp_path, "http://127.0.0.1:1")
    plan = copy.deepcopy(plan)
    if mutation in {"live", "controlled"}:
        plan["mode"] = mutation
    elif mutation == "all_fixture":
        for case in plan["cases"]:
            case["fixture_html"] = EMPTY
    else:
        plan["cases"][0]["fixture_html"] = EMPTY
    with pytest.raises(ValueError):
        Suite.model_validate(plan)
