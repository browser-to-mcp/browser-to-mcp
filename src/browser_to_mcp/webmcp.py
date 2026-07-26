"""Read-only discovery and snapshots for native WebMCP page tools."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright
from pydantic import BaseModel, ConfigDict, Field

_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class NativeDiscoveryStatus(StrEnum):
    """Stable outcome for one native WebMCP discovery attempt."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"
    ERROR = "error"


class PermissionPolicyState(StrEnum):
    """Observed state of the WebMCP ``tools`` Permissions Policy feature."""

    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


class NativePermissionPolicy(BaseModel):
    """Permissions Policy state captured alongside native tool metadata."""

    model_config = ConfigDict(extra="forbid")

    feature: str = "tools"
    state: PermissionPolicyState


class NativeDiscoveryDiagnostic(BaseModel):
    """A non-fatal discovery problem with a stable location and category."""

    model_config = ConfigDict(extra="forbid")

    code: str
    path: str
    message: str


class NativeToolMetadata(BaseModel):
    """Serializable metadata returned by WebMCP ``getTools``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, Any] | None = None
    origin: str | None = None
    annotations: dict[str, Any] | None = None
    valid: bool


class NativeDiscoverySnapshot(BaseModel):
    """Stable, non-executable snapshot of native tools exposed by a page."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    status: NativeDiscoveryStatus
    page_url: str
    page_origin: str
    permissions_policy: NativePermissionPolicy
    tools: list[NativeToolMetadata]
    diagnostics: list[NativeDiscoveryDiagnostic]
    snapshot_digest: str


class DiscoveryPage(Protocol):
    """Minimal page surface needed for read-only WebMCP discovery."""

    url: str

    async def evaluate(self, expression: str, arg: object = ...) -> object:
        """Evaluate the discovery-only page script."""


_DISCOVERY_SCRIPT = """
async ({ from_origins: fromOrigins }) => {
  const policy = document.permissionsPolicy ?? document.featurePolicy;
  let permissionState = "unknown";
  if (!policy || typeof policy.allowsFeature !== "function") {
    permissionState = "unsupported";
  } else {
    try {
      permissionState = policy.allowsFeature("tools") ? "allowed" : "denied";
    } catch (_) {
      permissionState = "unknown";
    }
  }

  const base = {
    page_url: location.href,
    page_origin: location.origin,
    permission_state: permissionState,
  };
  const modelContext = document.modelContext;
  if (!modelContext || typeof modelContext.getTools !== "function") {
    return {
      ...base,
      status: "unsupported",
      tools: [],
      diagnostics: [{
        code: "webmcp_unavailable",
        path: "document.modelContext",
        message: "The page does not expose the WebMCP discovery API.",
      }],
    };
  }

  try {
    const options = fromOrigins.length ? { fromOrigins } : {};
    const tools = await modelContext.getTools(options);
    if (!Array.isArray(tools)) {
      return {
        ...base,
        status: "error",
        tools: [],
        diagnostics: [{
          code: "invalid_tool_list",
          path: "document.modelContext.getTools",
          message: "WebMCP getTools did not return an array.",
        }],
      };
    }
    return {
      ...base,
      status: "supported",
      tools: tools.map((tool) => ({
        name: typeof tool?.name === "string" ? tool.name : null,
        title: typeof tool?.title === "string" ? tool.title : null,
        description: typeof tool?.description === "string" ? tool.description : null,
        input_schema: tool?.inputSchema ?? null,
        origin: typeof tool?.origin === "string" ? tool.origin : null,
        annotations: tool?.annotations ?? null,
      })),
      diagnostics: [],
    };
  } catch (error) {
    const name = typeof error?.name === "string" ? error.name : "Error";
    const blocked = permissionState === "denied" || name === "NotAllowedError";
    return {
      ...base,
      status: blocked ? "blocked" : "error",
      tools: [],
      diagnostics: [{
        code: blocked ? "permission_denied" : "discovery_failed",
        path: "document.modelContext.getTools",
        message: blocked
          ? "The WebMCP tools feature is blocked by Permissions Policy."
          : `WebMCP tool discovery failed with ${name}.`,
      }],
    };
  }
}
"""


async def discover_page_tools(
    page: DiscoveryPage,
    *,
    from_origins: Sequence[str] = (),
) -> NativeDiscoverySnapshot:
    """Collect native metadata from a page without invoking any native tool."""

    try:
        raw = await page.evaluate(
            _DISCOVERY_SCRIPT,
            {"from_origins": list(from_origins)},
        )
    except Exception as error:
        return _error_snapshot(
            _page_url(page),
            "evaluation_failed",
            f"WebMCP discovery evaluation failed with {type(error).__name__}.",
        )
    return _normalize_snapshot(raw, fallback_url=_page_url(page))


async def discover_url(
    url: str,
    *,
    trace_dir: Path | None = Path("traces"),
    timeout_seconds: int = 30,
    from_origins: Sequence[str] = (),
    playwright_factory: Any = async_playwright,
) -> tuple[NativeDiscoverySnapshot, str | None]:
    """Open a URL, capture native tool metadata, and optionally persist a snapshot."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")

    try:
        snapshot = await asyncio.wait_for(
            _discover_url(
                url,
                from_origins=from_origins,
                playwright_factory=playwright_factory,
                timeout_seconds=timeout_seconds,
            ),
            timeout=timeout_seconds,
        )
    except (TimeoutError, PlaywrightTimeoutError):
        snapshot = _error_snapshot(
            url,
            "discovery_timeout",
            f"Native WebMCP discovery exceeded {timeout_seconds} seconds.",
        )
    except Exception as error:
        snapshot = _error_snapshot(
            url,
            "browser_unavailable",
            f"Native WebMCP discovery could not start: {type(error).__name__}.",
        )

    trace_path = write_discovery_snapshot(snapshot, trace_dir) if trace_dir else None
    return snapshot, trace_path


async def _discover_url(
    url: str,
    *,
    from_origins: Sequence[str],
    playwright_factory: Any,
    timeout_seconds: int,
) -> NativeDiscoverySnapshot:
    async with playwright_factory() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            try:
                page = await context.new_page()
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_seconds * 1000,
                )
                return await discover_page_tools(page, from_origins=from_origins)
            finally:
                await context.close()
        finally:
            await browser.close()


def write_discovery_snapshot(
    snapshot: NativeDiscoverySnapshot,
    trace_dir: Path,
) -> str:
    """Persist a content-addressed native discovery snapshot and return its path."""

    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / f"webmcp-{snapshot.snapshot_digest.removeprefix('sha256:')}.json"
    path.write_text(
        json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _normalize_snapshot(raw: object, *, fallback_url: str) -> NativeDiscoverySnapshot:
    if not isinstance(raw, Mapping):
        return _error_snapshot(
            fallback_url,
            "invalid_discovery_result",
            "The page returned a non-object discovery result.",
        )

    page_url = _string_or(raw.get("page_url"), fallback_url)
    page_origin = _string_or(raw.get("page_origin"), _origin(page_url))
    permission_state = _permission_state(raw.get("permission_state"))
    diagnostics = _diagnostics(raw.get("diagnostics"))
    status = _status(raw.get("status"), diagnostics)
    raw_tools = raw.get("tools")
    tools: list[NativeToolMetadata] = []
    if not isinstance(raw_tools, list):
        diagnostics.append(
            NativeDiscoveryDiagnostic(
                code="invalid_tool_list",
                path="tools",
                message="The page returned a non-array tool list.",
            )
        )
    else:
        for index, raw_tool in enumerate(raw_tools):
            tools.append(_normalize_tool(raw_tool, index, diagnostics))

    seen_names: set[str] = set()
    normalized_tools: list[NativeToolMetadata] = []
    for index, tool in enumerate(tools):
        if tool.name is not None and tool.name in seen_names:
            diagnostics.append(
                NativeDiscoveryDiagnostic(
                    code="duplicate_tool_name",
                    path=f"tools[{index}].name",
                    message=f"Tool name '{tool.name}' appears more than once.",
                )
            )
            tool = tool.model_copy(update={"valid": False})
        if tool.name is not None:
            seen_names.add(tool.name)
        normalized_tools.append(tool)

    normalized_tools.sort(key=lambda tool: (tool.name is None, tool.name or ""))
    if status is NativeDiscoveryStatus.SUPPORTED and not raw_tools and not diagnostics:
        status = NativeDiscoveryStatus.SUPPORTED
    snapshot = NativeDiscoverySnapshot(
        status=status,
        page_url=page_url,
        page_origin=page_origin,
        permissions_policy=NativePermissionPolicy(state=permission_state),
        tools=normalized_tools,
        diagnostics=diagnostics,
        snapshot_digest="",
    )
    return _with_digest(snapshot)


def _normalize_tool(
    raw: object,
    index: int,
    diagnostics: list[NativeDiscoveryDiagnostic],
) -> NativeToolMetadata:
    path = f"tools[{index}]"
    if not isinstance(raw, Mapping):
        diagnostics.append(
            NativeDiscoveryDiagnostic(
                code="malformed_tool",
                path=path,
                message="Tool metadata must be an object.",
            )
        )
        return NativeToolMetadata(valid=False)

    valid = True
    name = _optional_string(raw.get("name"))
    if name is None:
        valid = False
        _add_diagnostic(diagnostics, "malformed_name", f"{path}.name", "Tool name is required.")
    elif not _TOOL_NAME_PATTERN.fullmatch(name):
        valid = False
        _add_diagnostic(
            diagnostics,
            "invalid_name",
            f"{path}.name",
            "Tool names must contain only ASCII letters, digits, underscores, hyphens, "
            "or dots and be at most 128 characters.",
        )

    description = _optional_string(raw.get("description"))
    if not description:
        valid = False
        _add_diagnostic(
            diagnostics,
            "malformed_description",
            f"{path}.description",
            "Tool description is required.",
        )

    origin = _optional_string(raw.get("origin"))
    if not origin:
        valid = False
        _add_diagnostic(
            diagnostics,
            "malformed_origin",
            f"{path}.origin",
            "Tool origin is required by the native discovery contract.",
        )

    input_schema, schema_valid = _input_schema(raw.get("input_schema"), path, diagnostics)
    valid = valid and schema_valid
    annotations = raw.get("annotations")
    if annotations is not None and not isinstance(annotations, Mapping):
        valid = False
        _add_diagnostic(
            diagnostics,
            "malformed_annotations",
            f"{path}.annotations",
            "Tool annotations must be an object when present.",
        )
        annotations = None
    elif isinstance(annotations, Mapping):
        for annotation_name in ("readOnlyHint", "untrustedContentHint"):
            if annotation_name in annotations and not isinstance(
                annotations[annotation_name], bool
            ):
                valid = False
                _add_diagnostic(
                    diagnostics,
                    "malformed_annotation_value",
                    f"{path}.annotations.{annotation_name}",
                    "Known tool annotation values must be booleans.",
                )

    return NativeToolMetadata(
        name=name,
        title=_optional_string(raw.get("title")),
        description=description,
        input_schema=input_schema,
        origin=origin,
        annotations=dict(annotations) if isinstance(annotations, Mapping) else None,
        valid=valid,
    )


def _input_schema(
    raw: object,
    path: str,
    diagnostics: list[NativeDiscoveryDiagnostic],
) -> tuple[dict[str, Any] | None, bool]:
    if raw is None or raw == "":
        return None, True
    parsed: object = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            _add_diagnostic(
                diagnostics,
                "malformed_input_schema",
                f"{path}.input_schema",
                "Tool inputSchema is not valid JSON.",
            )
            return None, False
    if not isinstance(parsed, dict):
        _add_diagnostic(
            diagnostics,
            "malformed_input_schema",
            f"{path}.input_schema",
            "Tool inputSchema must decode to a JSON object.",
        )
        return None, False
    return parsed, True


def _error_snapshot(url: str, code: str, message: str) -> NativeDiscoverySnapshot:
    snapshot = NativeDiscoverySnapshot(
        status=NativeDiscoveryStatus.ERROR,
        page_url=url,
        page_origin=_origin(url),
        permissions_policy=NativePermissionPolicy(state=PermissionPolicyState.UNKNOWN),
        tools=[],
        diagnostics=[NativeDiscoveryDiagnostic(code=code, path="discovery", message=message)],
        snapshot_digest="",
    )
    return _with_digest(snapshot)


def _with_digest(snapshot: NativeDiscoverySnapshot) -> NativeDiscoverySnapshot:
    payload = snapshot.model_dump(mode="json", exclude={"snapshot_digest"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
    return snapshot.model_copy(update={"snapshot_digest": digest})


def _diagnostics(raw: object) -> list[NativeDiscoveryDiagnostic]:
    if not isinstance(raw, list):
        return []
    diagnostics: list[NativeDiscoveryDiagnostic] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        code = _optional_string(item.get("code"))
        path = _optional_string(item.get("path"))
        message = _optional_string(item.get("message"))
        if code and path and message:
            diagnostics.append(
                NativeDiscoveryDiagnostic(code=code, path=path, message=message)
            )
    return diagnostics


def _status(raw: object, diagnostics: list[NativeDiscoveryDiagnostic]) -> NativeDiscoveryStatus:
    try:
        return NativeDiscoveryStatus(raw)
    except ValueError:
        diagnostics.append(
            NativeDiscoveryDiagnostic(
                code="invalid_status",
                path="status",
                message="The page returned an unsupported discovery status.",
            )
        )
        return NativeDiscoveryStatus.ERROR


def _permission_state(raw: object) -> PermissionPolicyState:
    try:
        return PermissionPolicyState(raw)
    except ValueError:
        return PermissionPolicyState.UNKNOWN


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _string_or(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _page_url(page: DiscoveryPage) -> str:
    return _string_or(getattr(page, "url", ""), "about:blank")


def _origin(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "null"
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "null"


def _add_diagnostic(
    diagnostics: list[NativeDiscoveryDiagnostic],
    code: str,
    path: str,
    message: str,
) -> None:
    diagnostics.append(NativeDiscoveryDiagnostic(code=code, path=path, message=message))
