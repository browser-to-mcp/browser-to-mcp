"""Extract one synchronous snapshot of row-aligned fields in a protected browser."""

import asyncio
from importlib.resources import files
from urllib.parse import urlsplit

from browser_to_mcp.browser_policy import safe_url
from browser_to_mcp.models import (
    MAX_CELL_CHARS,
    MAX_RECORD_BYTES,
    StructuredOutput,
    WorkflowStep,
)
from browser_to_mcp.structured_outputs import OutputValidationError


async def extract_records(page, step: WorkflowStep, output: StructuredOutput):
    rows, _ = await extract_record_snapshot(page, step, output)
    return rows


async def extract_record_snapshot(page, step: WorkflowStep, output: StructuredOutput):
    async def observe(operation):
        if step.paging:
            return await cancellable_page_operation(page, operation)
        return await operation

    config = {
        "area": step.selector,
        "rows": step.record.rows,
        "fields": {name: field.model_dump() for name, field in step.record.fields.items()},
        "empty": step.record.empty_selector,
        "limit": output.max_items if output.type == "array" else 1,
        "cellLimit": MAX_CELL_CHARS,
        "byteLimit": MAX_RECORD_BYTES,
        "paging": step.paging.model_dump() if step.paging else None,
    }
    script = files("browser_to_mcp").joinpath("record_extraction.js").read_text("utf-8")
    await observe(
        page.wait_for_function(
            script, arg={**config, "readyOnly": True}, timeout=step.timeout_seconds * 1000
        )
    )
    result = await observe(page.evaluate(script, {**config, "readyOnly": False}))
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
    if output.type == "object":
        if len(rows) != 1:
            raise OutputValidationError(f"outputs.{step.id}", "Expected exactly one record.")
        return rows[0], result.get("navigation")
    return rows, result.get("navigation")


async def cancellable_page_operation(page, operation):
    """Close and drain an in-flight browser command when its caller is cancelled.

    Let Playwright receive the page-closed response before discarding the command;
    otherwise its detached future can log an unhandled error containing the URL.
    """
    pending = asyncio.create_task(operation)
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        try:
            await page.close()
        finally:
            await asyncio.gather(pending, return_exceptions=True)
        raise
