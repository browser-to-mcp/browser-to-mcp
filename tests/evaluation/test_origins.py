"""The normal model-proposal compiler feeds the same source-independent test gate."""

import asyncio
import json

import pytest
from evaluation_site import candidate, suite

from browser_to_mcp.builder.contracts import check_task, make_candidate
from browser_to_mcp.evaluation.service import digest, evaluate, verify_evaluation
from browser_to_mcp.jobs.models import BuildReport


@pytest.mark.parametrize("provider", ["mcp_client", "openai", "llama_cpp"])
def test_compiled_proposal_and_imported_measurements_share_the_gate(
    evaluation_site, tmp_path, provider
):
    data = candidate(evaluation_site.base)
    request, _ = check_task(
        {
            "name": "compiled_catalog",
            "description": "Read matching products",
            "start_url": evaluation_site.base + "/catalog?q=notebook&color=all",
            "allowed_domains": ["127.0.0.1"],
            "inputs": {
                "query": {"description": "Search", "example": "notebook"},
                "color": {"description": "Color", "example": "all"},
            },
            "fields": {
                "title": {"description": "Full title"},
                "price_text": {"description": "Displayed price"},
                "product_url": {"description": "Product link", "link_path_prefix": "/products/"},
            },
            "shape": "array",
            "allow_empty": True,
            "unique_by": "product_url",
            "max_pages": 2,
            "max_items": 10,
            "timeout_seconds": 1,
        }
    )
    proposal = {
        "status": "draft",
        "area": "#results",
        "record": data["steps"][1]["record"],
        "bindings": {
            "query": {"location": "query", "key": "q"},
            "color": {"location": "query", "key": "color"},
        },
        "paging": {"next_selector": "a.next", "end_selector": ".end", "page_parameter": "page"},
    }
    # This is controlled proposal JSON, not a claim of model inference or a paid API call.
    _, content = make_candidate(request, json.dumps(proposal))
    workflow = tmp_path / "draft.candidate"
    workflow.write_bytes(content)
    plan = suite(evaluation_site.base)
    for case in plan["cases"]:
        expected = case["expected"]
        if expected["outcome"] == "outputs":
            expected["outputs"]["result"] = expected["outputs"].pop("products")
        if case["id"] == "invalid":
            # Task inputs have no minimum length; wrong JSON types still fail at the boundary.
            case["inputs"]["query"] = True
        if case["id"] == "changed_markup":
            expected["path"] = "outputs.result.0.price_text"
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    pin = digest(path.read_bytes())
    imported = BuildReport(
        provider=provider,
        model=None,
        prompt_tokens=None,
        completion_tokens=None,
        model_seconds=None,
        browser_seconds=0.0,
        api_cost_microusd=None,
        cost_basis="external_client"
        if provider == "mcp_client"
        else "configured_rates"
        if provider == "openai"
        else "local_no_api",
        checks=["schema", "policy", "compiler"],
    )
    directory = tmp_path / "attempt"
    report = asyncio.run(evaluate(workflow, path, directory, pin, generation=imported))
    assert report.eligible, report.blockers
    assert report.generation == imported
    assert report.generation.prompt_tokens is None and report.generation.api_cost_microusd is None
    assert verify_evaluation(workflow, path, directory, pin) == report
