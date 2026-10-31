"""One catalog interface over explicitly selected, published workflows."""

import asyncio
from typing import Annotated, Literal

from mcp.server.fastmcp.tools.base import Tool
from pydantic import Field, StrictInt, StrictStr, create_model

from browser_to_mcp.compiler import StrictArguments, StrictSchemaModel
from browser_to_mcp.models import StructuredOutput
from browser_to_mcp.server import (
    ServerConfigurationError,
    WorkflowFuncMetadata,
    _result,
    _workflow_tool,
)

CATALOGS = ("books", "laptops", "clothing")


class CatalogItem(StrictSchemaModel):
    title: Annotated[StrictStr, Field(min_length=1)]
    price_text: Annotated[StrictStr, Field(min_length=1)]
    product_url: Annotated[StrictStr, Field(min_length=1)]


class CatalogResult(StrictSchemaModel):
    catalog: Literal["books", "laptops", "clothing"]
    items: list[CatalogItem]
    pages_read: Annotated[StrictInt, Field(ge=1, le=20)]
    complete: bool
    stop_reason: Literal["end", "page_limit"]


def catalog_tool(compiled, bindings, *, trace_dir, timeout_seconds):
    """Pin catalog choices to the server's existing approved version snapshot."""
    selected = {}
    names = {item.name: item for item in compiled}
    for binding in bindings:
        catalog, separator, name = binding.partition("=")
        if not separator or catalog not in CATALOGS or catalog in selected or name not in names:
            raise ServerConfigurationError(
                "catalog", "Use distinct catalog=published_workflow_name bindings."
            )
        item = names[name]
        workflow = item.loaded.workflow
        steps = [step for step in workflow.steps if step.paging]
        if not item.loaded.publication or len(steps) != 1:
            raise ServerConfigurationError("catalog", "Each catalog needs a published paged tool.")
        step = steps[0]
        output = workflow.outputs.get(step.id)
        if (
            set(workflow.inputs) != {"max_pages"}
            or step.paging.max_pages_input != "max_pages"
            or step.paging.max_pages != 20
            or not isinstance(output, StructuredOutput)
            or output.type != "array"
            or set(output.record_fields) != {"title", "price_text", "product_url"}
            or any(field.type != "string" for field in output.record_fields.values())
            or set(workflow.outputs) != {step.id, step.paging.status_output}
            or step.record.unique_by != "product_url"
            or step.record.duplicates != "deduplicate"
            or step.record.fields["product_url"].source != "href"
            or any(
                not step.record.fields[key].normalize_whitespace for key in ("title", "price_text")
            )
        ):
            raise ServerConfigurationError(
                "catalog", "Catalogs need only max_pages (1–20), product rows and paging status."
            )
        selected[catalog] = (
            _workflow_tool(item, trace_dir=trace_dir, timeout_seconds=min(timeout_seconds, 120)),
            step.id,
            step.paging.status_output,
        )
    if "collect_catalog" in names or not selected:
        raise ServerConfigurationError("catalog", "The collect_catalog name must be available.")

    arguments = create_model(
        "CatalogArguments",
        __base__=StrictArguments,
        catalog=(Literal[tuple(selected)], ...),
        max_pages=(Annotated[StrictInt, Field(ge=1, le=20)], 20),
    )
    lock = asyncio.Lock()

    async def invoke(catalog, max_pages=20):
        tool, rows, status = selected[catalog]
        async with lock:
            response = await tool.run({"max_pages": max_pages})
        if response.isError:
            return response
        payload = response.structuredContent
        progress = payload["outputs"][status]
        if progress["stop_reason"] not in {"end", "page_limit"}:
            result = _result(
                dict(
                    category="assertion",
                    path="outputs." + rows,
                    message="Catalog exceeded its reviewed record limit.",
                    trace_path=payload["trace_path"],
                ),
                is_error=True,
            )
        else:
            result = _result(dict(catalog=catalog, items=payload["outputs"][rows], **progress))
        result.meta = {
            **(response.meta or {}),
            **{
                key: payload[key]
                for key in ("workflow", "manifest_digest", "trace_path", "duration_seconds")
            },
        }
        return result

    return Tool(
        fn=invoke,
        name="collect_catalog",
        description="Collect product names, displayed prices and links from an approved catalog.",
        parameters=arguments.model_json_schema(),
        fn_metadata=WorkflowFuncMetadata(
            arg_model=arguments,
            output_model=CatalogResult,
            output_schema=CatalogResult.model_json_schema(),
        ),
        is_async=True,
        annotations=next(iter(selected.values()))[0].annotations,
    )
