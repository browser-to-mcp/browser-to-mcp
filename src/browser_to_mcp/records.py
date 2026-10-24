"""Extract one synchronous snapshot of row-aligned fields in a protected browser."""

from importlib.resources import files
from urllib.parse import urlsplit, urlunsplit

from browser_to_mcp.browser_policy import safe_url
from browser_to_mcp.models import (
    MAX_CELL_CHARS,
    MAX_RECORD_BYTES,
    StructuredOutput,
    WorkflowStep,
)
from browser_to_mcp.structured_outputs import OutputValidationError


async def extract_records(page, step: WorkflowStep, output: StructuredOutput):
    config = {
        "area": step.selector,
        "rows": step.record.rows,
        "fields": {name: field.model_dump() for name, field in step.record.fields.items()},
        "empty": step.record.empty_selector,
        "limit": output.max_items if output.type == "array" else 1,
        "cellLimit": MAX_CELL_CHARS,
        "byteLimit": MAX_RECORD_BYTES,
    }
    script = files("browser_to_mcp").joinpath("record_extraction.js").read_text("utf-8")
    await page.wait_for_function(
        script, arg={**config, "readyOnly": True}, timeout=step.timeout_seconds * 1000
    )
    result = await page.evaluate(script, {**config, "readyOnly": False})
    if "error" in result:
        path = f"outputs.{step.id}"
        if result.get("row") is not None:
            path += f".{result['row']}"
        if result.get("field") is not None:
            path += f".{result['field']}"
        raise OutputValidationError(path, f"Record extraction failed: {result['error']}.")
    rows = result["records"]
    page_host = urlsplit(page.url).hostname
    for index, row in enumerate(rows):
        for name, spec in step.record.fields.items():
            if spec.source != "href" or row[name] is None:
                continue
            parsed = urlsplit(row[name])
            if (
                not safe_url(row[name])
                or parsed.hostname != page_host
                or not parsed.path.startswith(spec.path_prefix)
            ):
                raise OutputValidationError(
                    f"outputs.{step.id}.{index}.{name}",
                    "Product link is outside its reviewed host or path, or contains a secret.",
                )
            row[name] = urlunsplit(parsed._replace(fragment=""))
    if output.type == "object":
        if len(rows) != 1:
            raise OutputValidationError(f"outputs.{step.id}", "Expected exactly one record.")
        return rows[0]
    return rows
