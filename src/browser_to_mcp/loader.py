"""Load YAML workflow contracts and calculate canonical manifest digests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from browser_to_mcp.models import Workflow
from browser_to_mcp.validation import (
    FailureCategory,
    WorkflowValidationError,
    validate_workflow_policy,
)


@dataclass(frozen=True, slots=True)
class LoadedWorkflow:
    """A validated workflow and its deterministic identity digest."""

    workflow: Workflow
    manifest_digest: str


def load_workflow(path: Path) -> LoadedWorkflow:
    """Parse, validate, and policy-check a YAML workflow file."""

    try:
        raw_workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise WorkflowValidationError(FailureCategory.SCHEMA, str(path), str(error)) from error
    except yaml.YAMLError as error:
        raise WorkflowValidationError(FailureCategory.SCHEMA, str(path), str(error)) from error

    if not isinstance(raw_workflow, dict):
        raise WorkflowValidationError(
            FailureCategory.SCHEMA,
            str(path),
            "A workflow file must contain a YAML mapping.",
        )

    try:
        workflow = Workflow.model_validate(raw_workflow)
    except ValidationError as error:
        detail = error.errors()[0]
        path_hint = ".".join(str(part) for part in detail["loc"])
        raise WorkflowValidationError(
            FailureCategory.SCHEMA,
            f"{path}:{path_hint}",
            detail["msg"],
        ) from error

    validate_workflow_policy(workflow)
    return LoadedWorkflow(workflow=workflow, manifest_digest=manifest_digest(workflow))


def manifest_digest(workflow: Workflow) -> str:
    """Create a stable sha256 digest for all reviewed workflow behavior."""

    canonical = json.dumps(
        _canonicalize(workflow.model_dump(mode="json")),
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value
