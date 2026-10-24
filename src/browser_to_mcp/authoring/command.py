"""Guided terminal authoring; cancellation never publishes a workflow."""

from __future__ import annotations

import asyncio
import json
import keyword
import re
import shlex
import sys
from pathlib import Path

import yaml
from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError

from browser_to_mcp.authoring.browser import SelectionSession
from browser_to_mcp.authoring.export import export_workflow
from browser_to_mcp.authoring.plan import (
    AuthoringError,
    bind_input,
    binding_choices,
    build_workflow,
    input_definition,
    read_draft,
    recorded_steps,
    step_fields,
)
from browser_to_mcp.authoring.records import check_record_preview, configure_records
from browser_to_mcp.compiler import SchemaCompilationError
from browser_to_mcp.recorder.command import read_command
from browser_to_mcp.recorder.draft import Navigation
from browser_to_mcp.records import extract_records
from browser_to_mcp.replay import WorkflowExecutionError, process_outputs
from browser_to_mcp.structured_outputs import OutputValidationError
from browser_to_mcp.tracing import redact_outputs
from browser_to_mcp.validation import WorkflowValidationError


class Cancelled(Exception):
    pass


async def terminal_prompt(message: str) -> str:
    # Use the cancellable daemon reader: executor threads can hang on Ctrl+C at input().
    print(message, file=sys.stderr, flush=True)
    value = await read_command(preserve_case=True)
    if value.strip().lower() == "cancel":
        raise Cancelled
    return value.strip()


async def choice(prompt, message: str, options: set[str]) -> str:
    while True:
        value = await prompt(message)
        if value in options:
            return value
        print("Choose: " + ", ".join(sorted(options)), file=sys.stderr)


async def author(
    draft_path: Path, destination: Path, *, prompt=terminal_prompt, session_factory=SelectionSession
) -> int:
    draft_path = draft_path.expanduser()
    destination = destination.expanduser()
    if draft_path.resolve().is_relative_to(destination.resolve().parent):
        raise AuthoringError(
            "draft: Keep the recording outside the output workflow directory. "
            "Use a separate recordings folder."
        )
    if destination.exists() or destination.is_symlink():
        raise AuthoringError("destination: File already exists. Choose a new filename.")
    if destination.suffix not in {".yaml", ".yml"} or not destination.parent.is_dir():
        raise AuthoringError("destination: Choose a new .yaml file in an existing directory.")
    draft = read_draft(draft_path)
    print(
        "Type cancel at any prompt to discard this plan. No file is saved until final review.",
        file=sys.stderr,
    )
    observed = set()
    for index, event in enumerate(draft.events):
        print(f"Event {index}: {event.model_dump_json()}", file=sys.stderr)
        if index and isinstance(event, Navigation):
            answer = await choice(
                prompt,
                "Was this navigation caused by the preceding action? yes / no "
                "(no means you independently entered the URL)",
                {"yes", "no"},
            )
            if answer == "yes":
                observed.add(index)
        if (
            await choice(
                prompt,
                "Can you confirm this event only reads public information, needs no login, "
                "submits no form, and is safe to repeat? yes / no",
                {"yes", "no"},
            )
            != "yes"
        ):
            raise AuthoringError(f"events.{index}: Unreviewed actions cannot become safe steps.")
    original = recorded_steps(draft, observed)
    print(
        "Allowed domains (including their subdomains): " + ", ".join(draft.allowed_domains),
        file=sys.stderr,
    )
    if (
        await choice(
            prompt, "Replay these reviewed actions in a fresh browser? yes / no", {"yes", "no"}
        )
        != "yes"
    ):
        raise Cancelled
    while True:
        name = await prompt("Tool name (for example catalog_product_lookup):")
        if re.fullmatch(r"[a-z][a-z0-9_]*", name) and not keyword.iskeyword(name):
            break
        print(
            "name: Start with a lowercase letter; use letters, digits, and underscores.",
            file=sys.stderr,
        )
    description = ""
    while not description:
        description = await prompt("What does this tool do?")
    steps = [dict(step) for step in original]
    inputs = {}
    bound = set()
    while await choice(prompt, "Add an input? yes / no", {"yes", "no"}) == "yes":
        try:
            input_name = await prompt("Input name (for example product_slug):")
            if input_name in inputs:
                raise AuthoringError("inputs: Name already used. Choose a different name.")
            kind = await choice(
                prompt,
                "Type: string / integer / number / boolean",
                {"string", "integer", "number", "boolean"},
            )
            required = await choice(prompt, "Required? yes / no", {"yes", "no"}) == "yes"
            explanation = await prompt("Input description:")
            minimum = (
                await prompt("Minimum string length (blank for none):") if kind == "string" else ""
            )
            definition = input_definition(
                kind, required, explanation, int(minimum) if minimum else None
            )
            options = {}
            for i, step in enumerate(steps):
                for field, sample in binding_choices(step):
                    if (i, field) not in bound:
                        key = f"{i}/{field}"
                        options[key] = (i, field)
                        print(f"{key}: {sample}", file=sys.stderr)
            selected = await prompt(
                "Bind to one field above (or none for an unused optional input):"
            )
            if selected == "none" and not required:
                # Compile will still check the name, even without a binding.
                bind_input({"action": "fill", "value": "sample"}, "value", input_name)
            elif selected in options and required:
                i, field = options[selected]
                steps[i] = bind_input(steps[i], field, input_name)
                bound.add((i, field))
            else:
                raise AuthoringError(
                    "binding: Choose a listed field. Bound inputs must be required."
                )
            inputs[input_name] = definition
        except (AuthoringError, ValueError) as error:
            print(str(error), file=sys.stderr)
    async with session_factory(draft.events[0].url, draft.allowed_domains) as session:
        await session.prepare(original)
        outputs = {}
        previews = {}
        if (
            await choice(prompt, "Wait for visible text before extraction? yes / no", {"yes", "no"})
            == "yes"
        ):
            text = await prompt("Exact visible readiness text (fixed across runs):")
            if not text or "{{" in text:
                raise AuthoringError("wait: Enter non-empty fixed readiness text.")
            await session.page.get_by_text(text).first.wait_for(state="visible", timeout=10000)
            steps.append(step_fields("wait_for_text", "ready", text=text, timeout_seconds=10))
        while True:
            await session.reset_selection()
            await prompt("Click visible text to return in the browser, then press Enter here:")
            try:
                output_name = await prompt("Output name:")
                if output_name in outputs or output_name in {step["id"] for step in steps}:
                    raise AuthoringError("outputs: This name is already used. Choose another.")
                kind = await choice(
                    prompt,
                    "Output type: string / integer / number / boolean / array / object",
                    {"string", "integer", "number", "boolean", "array", "object"},
                )
                output_description = await prompt("Output description:")
                if kind in {"array", "object"}:
                    extraction, definition = await configure_records(
                        session, prompt, kind, output_name, output_description
                    )
                    candidate_steps = steps + [extraction]
                    candidate_outputs = {**outputs, output_name: definition}
                else:
                    target, preview = await session.selected()
                    print(f"Selected {target.selector}\nPreview: {preview}", file=sys.stderr)
                    candidate_steps = steps + [
                        step_fields("extract_text", output_name, selector=target.selector)
                    ]
                    candidate_outputs = {
                        **outputs,
                        output_name: dict(type=kind, description=output_description),
                    }
                loaded = build_workflow(
                    name,
                    description,
                    draft.allowed_domains,
                    candidate_steps,
                    inputs,
                    candidate_outputs,
                )
                if kind in {"array", "object"}:
                    preview = await extract_records(
                        session.page,
                        loaded.workflow.steps[-1],
                        loaded.workflow.outputs[output_name],
                    )
                    session.check_boundary()
                    check_record_preview(preview)
                validated = process_outputs(loaded.workflow, {**previews, output_name: preview})
                if kind in {"array", "object"}:
                    print(
                        "Record preview: "
                        + json.dumps(
                            redact_outputs(validated, loaded.workflow, {}), ensure_ascii=False
                        ),
                        file=sys.stderr,
                    )
                    if (
                        await choice(
                            prompt, "Are all rows and fields correct? yes / no", {"yes", "no"}
                        )
                        != "yes"
                    ):
                        raise AuthoringError("records: The preview was not approved. Select again.")
                steps, outputs = candidate_steps, candidate_outputs
                previews[output_name] = preview
            except ValidationError as error:
                # Validation metadata may contain a private URL the user mistyped.
                # Surface only our safe explanation, not Pydantic's input-value repr.
                print("output: " + error.errors()[0]["msg"], file=sys.stderr)
                continue
            except (
                AuthoringError,
                SchemaCompilationError,
                WorkflowValidationError,
                WorkflowExecutionError,
                OutputValidationError,
                ValueError,
            ) as error:
                print(str(error), file=sys.stderr)
                continue
            if await choice(prompt, "Add another output? yes / no", {"yes", "no"}) == "no":
                break
        session.check_boundary()
    loaded = build_workflow(name, description, draft.allowed_domains, steps, inputs, outputs)
    print(
        "Review the full workflow, including every step, domain, and success check:\n"
        + yaml.safe_dump(
            loaded.workflow.model_dump(mode="json", exclude_none=True),
            sort_keys=False,
            allow_unicode=True,
        ),
        file=sys.stderr,
    )
    if await prompt("Type save to approve this complete plan and create the workflow:") != "save":
        raise Cancelled
    path = export_workflow(loaded, destination, reviewed=True)
    print(
        json.dumps(
            dict(
                status="saved",
                workflow=loaded.workflow.name,
                path=str(path),
                manifest_digest=loaded.manifest_digest,
            )
        )
    )
    print(
        "Start or restart your MCP server: browser-to-mcp server --workflow-dir "
        + shlex.quote(str(path.parent)),
        file=sys.stderr,
    )
    return 0


def run_author(draft: Path, destination: Path) -> int:
    try:
        return asyncio.run(author(draft, destination))
    except PlaywrightError:
        print(
            "browser: Could not finish. Check the page and retry authoring; no file was saved.",
            file=sys.stderr,
        )
        return 2
    except (Cancelled, KeyboardInterrupt):
        print(json.dumps({"status": "cancelled", "saved": False}))
        return 1
    except (
        AuthoringError,
        SchemaCompilationError,
        WorkflowValidationError,
        WorkflowExecutionError,
    ) as error:
        print(
            json.dumps({"status": "failed", "category": "authoring", "message": str(error)}),
            file=sys.stderr,
        )
        return 2
