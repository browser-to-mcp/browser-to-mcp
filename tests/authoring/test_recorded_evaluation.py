"""A real captured and reviewed recording uses the same independent evidence gate."""

import asyncio
import json

from journey import create_workflow

from browser_to_mcp.evaluation.service import digest, evaluate, verify_evaluation


def test_recorder_export_passes_the_shared_gate(catalog, tmp_path):
    workflow = asyncio.run(create_workflow(catalog, tmp_path))
    cases = []
    for name, tag, slug, title, price in (
        ("shown", "demonstration", "a-light-in-the-attic_1000", "A Light in the Attic", "£51.77"),
        (
            "different",
            "changed_inputs",
            "shakespeares-sonnets_989",
            "Shakespeare's Sonnets",
            "£20.66",
        ),
    ):
        cases.append(
            {
                "id": name,
                "covers": [tag],
                "inputs": {"product_slug": slug},
                "expected": {"outcome": "outputs", "outputs": {"title": title, "price": price}},
            }
        )
    cases += [
        {
            "id": "empty_input",
            "covers": ["invalid_input"],
            "inputs": {"product_slug": ""},
            "expected": {"outcome": "error", "category": "input", "path": "inputs"},
        },
        {
            "id": "changed_page",
            "covers": ["changed_page"],
            "inputs": {"product_slug": "changed-layout"},
            "expected": {"outcome": "error", "category": "runtime", "path": "steps.title"},
        },
    ]
    plan = tmp_path / "reviewed-plan.json"
    plan.write_text(
        json.dumps(
            {
                "name": "Recorded book lookup",
                "mode": "controlled",
                "requirements_source": "PILOT.md: two known books and explicit failures",
                "expectations_source": "Fixed titles and prices, independent of recording",
                "cases": cases,
                "not_applicable": {
                    "combined_filters": "One input only",
                    "zero_results": "Scalar lookup, not a list",
                    "multiple_pages": "One product page per lookup",
                },
            }
        )
    )
    pin = digest(plan.read_bytes())
    directory = tmp_path / "evaluation"
    report = asyncio.run(evaluate(workflow, plan, directory, pin))
    assert report.eligible, report.blockers
    assert report.generation is None
    assert verify_evaluation(workflow, plan, directory, pin) == report
