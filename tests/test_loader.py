from __future__ import annotations

from pathlib import Path

import pytest

from browser_to_mcp.loader import load_workflow, manifest_digest
from browser_to_mcp.validation import FailureCategory, WorkflowValidationError

WORKFLOW_PATH = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"


def test_loads_reference_workflow() -> None:
    loaded = load_workflow(WORKFLOW_PATH)

    assert loaded.workflow.name == "hn_search"
    assert loaded.manifest_digest.startswith("sha256:")


def test_manifest_digest_is_stable() -> None:
    first = load_workflow(WORKFLOW_PATH).workflow
    second = load_workflow(WORKFLOW_PATH).workflow

    assert manifest_digest(first) == manifest_digest(second)


def test_rejects_non_read_only_workflow(tmp_path: Path) -> None:
    invalid = tmp_path / "write.yaml"
    source = WORKFLOW_PATH.read_text(encoding="utf-8")
    invalid.write_text(
        source.replace("risk: read_only", "risk: financial"),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.POLICY


def test_rejects_unauthorized_domain(tmp_path: Path) -> None:
    invalid = tmp_path / "domain.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace("hn.algolia.com/?q", "example.com/?q"),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.PERMISSION


def test_rejects_assertion_for_undeclared_output(tmp_path: Path) -> None:
    invalid = tmp_path / "assertion.yaml"
    invalid.write_text(
        WORKFLOW_PATH.read_text(encoding="utf-8").replace("field: results", "field: missing"),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowValidationError) as error:
        load_workflow(invalid)

    assert error.value.category is FailureCategory.SCHEMA
