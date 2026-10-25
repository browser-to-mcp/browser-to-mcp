"""Prepare bounded public workflow data using the normal schema and policy."""

import re

import yaml

from browser_to_mcp.browser_policy import SECRET_VALUE
from browser_to_mcp.loader import LoadedWorkflow, manifest_digest
from browser_to_mcp.models import Workflow
from browser_to_mcp.validation import validate_workflow_policy

from .models import JobError

MAX_SOURCE_BYTES = 262144
ASSIGNMENT = re.compile(r"\b(?:password|passwd|token|secret|api[_ -]?key)\s*[:=]\s*\S+", re.I)


def check_candidate(source: str) -> tuple[LoadedWorkflow, bytes]:
    try:
        if not isinstance(source, str) or len(source.encode()) > MAX_SOURCE_BYTES:
            raise ValueError
        for index, token in enumerate(yaml.scan(source)):
            if index > 20000 or isinstance(token, (yaml.AliasToken, yaml.AnchorToken)):
                raise ValueError
        raw = yaml.safe_load(source)
        workflow = Workflow.model_validate(raw)
        validate_workflow_policy(workflow)
        normalized = yaml.safe_dump(
            workflow.model_dump(mode="json", exclude_none=True), sort_keys=False, allow_unicode=True
        )
        if SECRET_VALUE.search(normalized) or ASSIGNMENT.search(normalized):
            raise ValueError
        data = normalized.encode()
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError
        return LoadedWorkflow(workflow, manifest_digest(workflow)), data
    except Exception:
        # Parser and validation errors can quote private source values.
        raise JobError("invalid_candidate") from None
