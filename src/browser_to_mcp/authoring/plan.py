"""Pure authoring operations. No browser or file is changed by building a plan."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.loader import LoadedWorkflow, manifest_digest
from browser_to_mcp.models import Workflow, WorkflowInput, schema_field_name_error
from browser_to_mcp.recorder.capture import Capture
from browser_to_mcp.recorder.draft import (
    Diagnostic,
    Navigation,
    RecordingDraft,
    RecordingError,
    validate_start,
)
from browser_to_mcp.url_templates import TEMPLATE_PATTERN, validate_url_template
from browser_to_mcp.validation import validate_workflow_policy


class AuthoringError(ValueError):
    """A correction the author can make without exposing raw browser details."""


def read_draft(path: Path) -> RecordingDraft:
    try:
        draft = RecordingDraft.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        raise AuthoringError(
            "draft: Cannot read this versioned recording. Record a new draft."
        ) from None
    validate_draft(draft)
    return draft


def validate_draft(draft: RecordingDraft) -> None:
    if (
        draft.status != "captured"
        or not draft.events
        or any(isinstance(event, Diagnostic) for event in draft.events)
    ):
        raise AuthoringError("draft: Incomplete recordings cannot be exported. Record again.")
    if not isinstance(draft.events[0], Navigation):
        raise AuthoringError("events.0: The recording must start with a navigation.")
    checked = Capture(draft.allowed_domains)
    for index, event in enumerate(draft.events):
        checked.accept(event.model_dump())
        if checked.incomplete:
            raise AuthoringError(f"events.{index}: Unsafe recording data. Record a new draft.")
        if isinstance(event, Navigation):
            try:
                validate_start(event.url, draft.allowed_domains)
            except RecordingError:
                raise AuthoringError(
                    f"events.{index}.url: Unsafe or undeclared URL. "
                    "Record again with explicit domains."
                ) from None
        # Inspect values, not serialized JSON: nested objects naturally end with }}.
        texts = [getattr(event, field, "") for field in ("url", "value", "key")]
        if hasattr(event, "target"):
            texts.append(event.target.selector)
        if any("{{" in text or "}}" in text for text in texts):
            raise AuthoringError(
                f"events.{index}: Recorded templates are unsupported. Record again."
            )


def step_fields(action: str, step_id: str, **fields) -> dict:
    return dict(
        id=step_id,
        action=action,
        timeout_seconds=fields.pop("timeout_seconds", 5 if action != "goto" else 30),
        risk="read_only",
        idempotency="safe",
        retry="none",
        confirmation="never",
        **fields,
    )


def recorded_steps(draft: RecordingDraft, observed_navigations: set[int]) -> list[dict]:
    """Navigation observations after clicks must be explicitly classified by the author."""
    validate_draft(draft)
    if any(
        i <= 0 or i >= len(draft.events) or not isinstance(draft.events[i], Navigation)
        for i in observed_navigations
    ):
        raise AuthoringError("events: Only later navigation observations may be skipped.")
    steps = []
    for index, event in enumerate(draft.events):
        if index in observed_navigations:
            continue
        if isinstance(event, Navigation):
            fields = {"url": event.url}
            action = "goto"
        else:
            fields = event.model_dump(exclude={"kind", "target"})
            fields["selector"] = event.target.selector
            action = event.kind
        steps.append(step_fields(action, f"recorded_{index}", **fields))
    return steps


def binding_choices(step: dict) -> list[tuple[str, str]]:
    """Address one exact recorded field; repeated values are never globally replaced."""
    if step["action"] == "fill":
        return [("value", step["value"])]
    if step["action"] != "goto":
        return []
    parsed = urlsplit(step["url"])
    choices = [(f"path:{i}", value) for i, value in enumerate(parsed.path.split("/")) if value]
    choices += [
        (f"query:{i}", part.partition("=")[2])
        for i, part in enumerate(parsed.query.split("&"))
        if "=" in part
    ]
    return choices


def bind_input(step: dict, field: str, name: str) -> dict:
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", name) or schema_field_name_error(name):
        raise AuthoringError("inputs: Use a name such as product_slug, without spaces or keywords.")
    if field not in dict(binding_choices(step)):
        raise AuthoringError("binding: Choose a fill value, URL path segment, or query value.")
    result = dict(step)
    placeholder = "{{" + name + "}}"
    if field == "value":
        result["value"] = placeholder
    else:
        kind, index = field.split(":")
        parsed = urlsplit(step["url"])
        if kind == "path":
            parts = parsed.path.split("/")
            parts[int(index)] = placeholder
            parsed = parsed._replace(path="/".join(parts))
        else:
            parts = parsed.query.split("&")
            parts[int(index)] = parts[int(index)].partition("=")[0] + "=" + placeholder
            parsed = parsed._replace(query="&".join(parts))
        result["url"] = urlunsplit(parsed)
        validate_url_template(result["url"])
    return result


def build_workflow(
    name: str, description: str, domains: list[str], steps: list[dict], inputs: dict, outputs: dict
) -> LoadedWorkflow:
    raw = dict(
        name=name,
        description=description,
        version=1,
        risk="read_only",
        manifest={"version": 1},
        permissions={"allowed_domains": domains},
        inputs=inputs,
        steps=steps,
        outputs=outputs,
        success=[
            dict(
                id=f"check_{key}",
                field=key,
                assertion="output_matches_schema"
                if definition.get("allow_empty")
                else "output_not_empty",
            )
            for key, definition in outputs.items()
        ],
    )
    try:
        workflow = Workflow.model_validate(raw)
    except ValidationError as error:
        detail = error.errors()[0]
        raise AuthoringError(f"{'.'.join(map(str, detail['loc']))}: {detail['msg']}") from None
    for step in workflow.steps:
        for field in (step.url, step.value, step.selector, step.text, step.key):
            for referenced in TEMPLATE_PATTERN.findall(field or ""):
                if referenced not in workflow.inputs:
                    raise AuthoringError(f"steps.{step.id}: Declare input '{referenced}'.")
                if not workflow.inputs[referenced].required:
                    raise AuthoringError(
                        f"inputs.{referenced}: A value used by a step must be required; "
                        "V0 has no conditional steps or fallback values."
                    )
    validate_workflow_policy(workflow)
    loaded = LoadedWorkflow(workflow, manifest_digest(workflow))
    compile_workflow(loaded)
    return loaded


def input_definition(
    type_name: str, required: bool, description: str, min_length: int | None
) -> dict:
    try:
        return WorkflowInput(
            type=type_name, required=required, description=description, min_length=min_length
        ).model_dump(exclude_none=True)
    except ValidationError:
        raise AuthoringError(
            "inputs: Choose string, integer, number, or boolean. "
            "Only strings support a positive minimum length."
        ) from None
