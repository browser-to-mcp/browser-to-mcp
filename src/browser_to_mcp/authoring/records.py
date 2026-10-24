"""Choose a result area, one repeated row, and fields without typing CSS."""

import json
import sys

from browser_to_mcp.authoring.plan import AuthoringError, build_workflow, step_fields
from browser_to_mcp.browser_policy import SECRET_VALUE
from browser_to_mcp.models import (
    RecordExtraction,
    RecordField,
    StructuredOutput,
    schema_field_name_error,
)
from browser_to_mcp.records import extract_records
from browser_to_mcp.replay import process_outputs
from browser_to_mcp.tracing import redact_outputs


def check_record_preview(value):
    rows = value if isinstance(value, list) else [value]
    if any(
        isinstance(cell, str) and SECRET_VALUE.search(cell) for row in rows for cell in row.values()
    ):
        raise AuthoringError(
            "records: Credential-like values cannot be previewed. Choose public data."
        )


async def _choice(prompt, message, options):
    while True:
        value = await prompt(message)
        if value in options:
            return value
        print("Choose: " + ", ".join(sorted(options)), file=sys.stderr)


async def _pick_candidate(session, prompt, mode, *, minimum=1):
    candidates = await session.inspect_record(mode + "s", minimum=minimum)
    if not candidates:
        raise AuthoringError("records: No suitable area or repeated row was found. Select again.")
    for index, candidate in enumerate(candidates, 1):
        count = f", {candidate['count']} matching rows" if "count" in candidate else ""
        print(f"{index}: surrounding level {candidate['depth']}{count}", file=sys.stderr)
    selected = await _choice(
        prompt, f"Choose the {mode} number to use:", {str(i) for i in range(1, len(candidates) + 1)}
    )
    return (await session.inspect_record(mode, index=int(selected) - 1))["selector"]


async def configure_records(session, prompt, kind, name, description):
    area = await _pick_candidate(session, prompt, "area")
    await session.reset_selection()
    await prompt("Click inside one example product row, then press Enter here:")
    rows = await _pick_candidate(session, prompt, "row", minimum=2 if kind == "array" else 1)
    maximum = 1
    if kind == "array":
        raw = await prompt("Maximum rows to read (1 to 100; blank means 50):")
        try:
            maximum = int(raw) if raw else 50
            if not 1 <= maximum <= 100:
                raise ValueError
        except ValueError:
            raise AuthoringError("records: Enter a whole row limit from 1 to 100.") from None
    properties = {}
    fields = {}
    while True:
        field = await prompt("Record field name (for example title or price_text):")
        if schema_field_name_error(field) or len(field) > 64 or field in fields:
            raise AuthoringError(
                "records: Use a unique public field name of at most 64 characters."
            )
        scalar = await _choice(
            prompt,
            "Field type: string / integer / number / boolean",
            {"string", "integer", "number", "boolean"},
        )
        source = await _choice(
            prompt,
            "Read visible text, full title, or link? text / title / href",
            {"text", "title", "href"},
        )
        if source in {"title", "href"} and scalar != "string":
            raise AuthoringError("records: Title attributes and links must use string fields.")
        prefix = (
            await prompt("Allowed product-link path prefix (for example /products/):")
            if source == "href"
            else None
        )
        nullable = (
            await _choice(prompt, "Allow missing or blank value as null? yes / no", {"yes", "no"})
            == "yes"
        )
        sensitive = (
            await _choice(prompt, "Hide this field in traces and previews? yes / no", {"yes", "no"})
            == "yes"
        )
        await session.reset_selection()
        await prompt("Click this field inside the same example row, then press Enter here:")
        selected = await session.inspect_record("field", source=source, nullable=nullable)
        fields[field] = dict(selector=selected["selector"], source=source)
        if prefix is not None:
            fields[field]["path_prefix"] = prefix
        properties[field] = RecordField(
            type=scalar, nullable=nullable, sensitive=sensitive
        ).model_dump()
        if (
            len(fields) == 16
            or await _choice(prompt, "Add another record field? yes / no", {"yes", "no"}) == "no"
        ):
            break
    duplicates = await _choice(
        prompt, "Duplicate rows: reject / keep / deduplicate", {"reject", "keep", "deduplicate"}
    )
    unique_by = None
    if duplicates != "keep":
        key = await prompt(
            "Unique field name (blank compares whole records; required to deduplicate):"
        )
        unique_by = key or None
    allow_empty = (
        kind == "array"
        and await _choice(prompt, "Allow a confirmed empty results list? yes / no", {"yes", "no"})
        == "yes"
    )
    empty_selector = None
    if allow_empty:
        # Validate populated rows before the user visits an empty state. An empty
        # preview alone cannot prove that the selected fields align with real rows.
        sample_definition = dict(
            type="array",
            items={"type": "object", "properties": properties},
            max_items=maximum,
        )
        sample_step = step_fields(
            "extract_records",
            name,
            selector=area,
            record=dict(
                rows=rows,
                fields=fields,
                duplicates=duplicates,
                unique_by=unique_by,
            ),
        )
        sample = build_workflow(
            "record_preview",
            "Review rows",
            session.capture.domains,
            [sample_step],
            {},
            {name: sample_definition},
        )
        raw = await extract_records(
            session.page, sample.workflow.steps[0], sample.workflow.outputs[name]
        )
        session.check_boundary()
        check_record_preview(raw)
        typed = process_outputs(sample.workflow, {name: raw})
        print(
            "Populated preview: " + json.dumps(redact_outputs(typed, sample.workflow, {})),
            file=sys.stderr,
        )
        if (
            await _choice(prompt, "Are the populated rows correct? yes / no", {"yes", "no"})
            != "yes"
        ):
            raise AuthoringError("records: The populated preview was not approved.")
        await prompt("Open a permitted empty-results page using the address bar, then press Enter:")
        await session.page.wait_for_load_state("domcontentloaded", timeout=10000)
        await session.install_selection()
        await session.reset_selection()
        await prompt("Click its empty-results message, then press Enter here:")
        selected, _ = await session.selected()
        valid = await session.page.evaluate(
            """([area, marker]) => {
          const roots = document.querySelectorAll(area);
          const markers = document.querySelectorAll(marker);
          return roots.length === 1 && markers.length === 1 &&
            roots[0] !== markers[0] && roots[0].contains(markers[0]);
        }""",
            [area, selected.selector],
        )
        if not valid:
            raise AuthoringError("records: The empty message must be inside the same results area.")
        empty_selector = selected.selector
    extraction = RecordExtraction(
        rows=rows,
        fields=fields,
        unique_by=unique_by,
        duplicates=duplicates,
        empty_selector=empty_selector,
    )
    definition = StructuredOutput(
        type=kind,
        description=description,
        properties=properties if kind == "object" else None,
        items={"type": "object", "properties": properties} if kind == "array" else None,
        max_items=maximum if kind == "array" else None,
        allow_empty=allow_empty,
    )
    return step_fields(
        "extract_records", name, selector=area, record=extraction.model_dump()
    ), definition.model_dump(exclude_none=True)
