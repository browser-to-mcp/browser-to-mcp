from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from pydantic import ValidationError

from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.loader import LoadedWorkflow, load_workflow
from browser_to_mcp.models import Workflow, WorkflowInput, WorkflowOutput
from browser_to_mcp.replay import (
    ExecutionFailureCategory,
    PlaywrightReplayEngine,
    WorkflowExecutionError,
    evaluate_success_assertions,
    process_outputs,
)
from browser_to_mcp.server import ServerConfigurationError, create_server, load_workflows
from browser_to_mcp.tracing import redact_inputs, redact_value
from browser_to_mcp.validation import (
    FailureCategory,
    WorkflowValidationError,
    validate_workflow_policy,
)

SOURCE = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


@pytest.mark.parametrize(
    ("type_name", "raw", "expected"),
    [
        ("string", "  title\nwith whitespace  ", "  title\nwith whitespace  "),
        ("string", "$19.99", "$19.99"),
        ("string", "available", "available"),
        ("string", "false", "false"),
        ("integer", "12", 12),
        ("integer", " \n-12\t", -12),
        ("integer", "0", 0),
        ("integer", "9007199254740993", 9007199254740993),
        ("number", "12", 12.0),
        ("number", " 19.99\n", 19.99),
        ("number", "-1.25e2", -125.0),
        ("number", "0", 0.0),
        ("number", "-0.0", -0.0),
        ("boolean", "true", True),
        ("boolean", " \nfalse\t", False),
    ],
)
def test_output_conversion_matches_mcp_schema(type_name: str, raw: str, expected: object) -> None:
    workflow = load_workflow(SOURCE).workflow.model_copy(deep=True)
    workflow.outputs["results"] = WorkflowOutput(type=type_name)
    outputs = process_outputs(workflow, {"results": raw})

    assert outputs["results"] == expected
    assert type(outputs["results"]) is type(expected)
    assertions = evaluate_success_assertions(workflow.success, outputs)
    assert str(assertions[0].status) == "passed"
    compiled = compile_workflow(LoadedWorkflow(workflow, "sha256:example"))
    validated = compiled.output_model.model_validate(
        {
            "workflow": workflow.name,
            "manifest_digest": "sha256:example",
            "outputs": outputs,
            "trace_path": None,
            "duration_seconds": 0.0,
        }
    ).model_dump(mode="json")
    assert type(validated["outputs"]["results"]) is type(expected)
    assert json.loads(json.dumps(validated))["outputs"] == outputs


@pytest.mark.parametrize(
    ("type_name", "raw"),
    [
        ("string", ""),
        ("string", " \n\t"),
        ("integer", "1.0"),
        ("integer", "1e2"),
        ("integer", "+12"),
        ("integer", "012"),
        ("integer", "1_000"),
        ("integer", '"12"'),
        ("integer", "true"),
        ("integer", "unknown"),
        ("number", "$19.99"),
        ("number", "1,234"),
        ("number", ".5"),
        ("number", "1."),
        ("number", "NaN"),
        ("number", "Infinity"),
        ("number", "-Infinity"),
        ("number", "1e9999"),
        ("number", "1e-9999"),
        ("number", "1e9999999999999999999999999999999"),
        ("number", "9007199254740993"),
        ("number", "9007199254740993e0"),
        ("number", "0.10000000000000001"),
        ("number", "true"),
        ("boolean", "True"),
        ("boolean", "FALSE"),
        ("boolean", "available"),
        ("boolean", "0"),
        ("boolean", "1"),
        ("boolean", '"false"'),
        ("integer", "null"),
        ("number", "[]"),
        ("boolean", "{}"),
    ],
)
def test_invalid_outputs_fail_without_echoing_page_values(type_name: str, raw: str) -> None:
    workflow = load_workflow(SOURCE).workflow.model_copy(deep=True)
    workflow.outputs["results"] = WorkflowOutput(type=type_name)

    with pytest.raises(WorkflowExecutionError) as error:
        process_outputs(workflow, {"results": raw})

    assert error.value.category is ExecutionFailureCategory.ASSERTION
    assert error.value.path == "outputs.results"
    assert error.value.message.startswith("Output must contain ")
    if raw.strip():
        assert raw not in error.value.message


@pytest.mark.parametrize("outputs", [{}, {"results": "title", "extra": "value"}])
def test_output_shape_is_closed_and_required(outputs: dict[str, str]) -> None:
    workflow = load_workflow(SOURCE).workflow
    with pytest.raises(WorkflowExecutionError) as error:
        process_outputs(workflow, outputs)
    assert error.value.category is ExecutionFailureCategory.ASSERTION
    assert error.value.path == ("outputs.extra" if "extra" in outputs else "outputs.results")


def test_missing_producer_cannot_start_browser_or_register_tools(
    monkeypatch, tmp_path: Path
) -> None:
    workflow = load_workflow(SOURCE).workflow.model_copy(deep=True)
    workflow.steps.pop()
    browser_start = Mock(
        side_effect=AssertionError("Invalid output mappings cannot start browsers.")
    )
    register_tool = Mock(
        side_effect=AssertionError("Invalid output mappings cannot register tools.")
    )
    monkeypatch.setattr("browser_to_mcp.replay.async_playwright", browser_start)
    monkeypatch.setattr("browser_to_mcp.server._workflow_tool", register_tool)

    with pytest.raises(WorkflowValidationError) as error:
        asyncio.run(PlaywrightReplayEngine().run(workflow, {"query": "example"}))
    assert error.value.category is FailureCategory.SCHEMA
    assert error.value.path == "outputs.results"
    with pytest.raises(ServerConfigurationError) as server_error:
        create_server([LoadedWorkflow(workflow, "sha256:invalid")])
    assert server_error.value.path == "outputs.results"
    (tmp_path / "invalid.yaml").write_text(
        yaml.safe_dump(workflow.model_dump(mode="json")), encoding="utf-8"
    )
    with pytest.raises(ServerConfigurationError, match="outputs.results"):
        load_workflows(tmp_path)
    browser_start.assert_not_called()
    register_tool.assert_not_called()


def test_recursively_redact_text_without_changing_other_primitive_types() -> None:
    workflow = load_workflow(SOURCE).workflow.model_copy(deep=True)
    workflow.inputs["query"].sensitive = True
    workflow.inputs["public"] = WorkflowInput(type="string")
    inputs = {"query": "C++ & secret", "public": "echo C++ & secret"}
    value = {
        "nested": [{"text": "C++ & secret", "url": "C%2B%2B%20%26%20secret"}],
        "integer": 0,
        "number": 1.25,
        "boolean": False,
    }

    assert redact_value(value, workflow, inputs) == {
        "nested": [{"text": "[REDACTED]", "url": "[REDACTED]"}],
        "integer": 0,
        "number": 1.25,
        "boolean": False,
    }
    assert redact_inputs(workflow, inputs) == {
        "query": "[REDACTED]",
        "public": "echo [REDACTED]",
    }


def test_sensitive_boolean_text_uses_the_same_rendering_as_workflow_inputs() -> None:
    workflow = load_workflow(SOURCE).workflow.model_copy(deep=True)
    workflow.inputs["query"] = WorkflowInput(type="boolean", sensitive=True)
    assert redact_value(["false", "False", False], workflow, {"query": False}) == [
        "[REDACTED]",
        "[REDACTED]",
        False,
    ]


def test_documented_multiple_output_workflow_validates() -> None:
    text = (SOURCE.parents[1] / "docs" / "OUTPUTS.md").read_text(encoding="utf-8")
    yaml_text = text.split("```yaml\n", 1)[1].split("```", 1)[0]
    workflow = Workflow.model_validate(yaml.safe_load(yaml_text))
    validate_workflow_policy(workflow)
    compile_workflow(LoadedWorkflow(workflow, "sha256:example"))
    assert set(workflow.outputs) == {"title", "stock_count", "price", "available"}


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_generated_mcp_schema_rejects_non_finite_numbers(number: float) -> None:
    workflow = load_workflow(SOURCE).workflow.model_copy(deep=True)
    workflow.outputs["results"] = WorkflowOutput(type="number")
    compiled = compile_workflow(LoadedWorkflow(workflow, "sha256:example"))
    with pytest.raises(ValidationError):
        compiled.output_model.model_validate(
            {
                "workflow": workflow.name,
                "manifest_digest": "sha256:example",
                "outputs": {"results": number},
                "trace_path": None,
                "duration_seconds": 0.0,
            }
        )
