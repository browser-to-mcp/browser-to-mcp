"""Versioned recording data and exclusive, private local draft persistence."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from browser_to_mcp.browser_policy import (
    MESSAGES,
    safe_url,
)
from browser_to_mcp.browser_policy import (
    is_allowed_url as _is_allowed_url,
)
from browser_to_mcp.models import Permissions

DRAFT_SUFFIX = ".recording.json"


class RecordingError(ValueError):
    """A safe, user-facing recorder error; never wrap raw browser exceptions."""


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Target(DraftModel):
    selector: str = Field(min_length=1, max_length=512)
    strategy: Literal[
        "data-testid",
        "data-test",
        "data-qa",
        "id",
        "aria-label",
        "label",
        "name",
        "placeholder",
        "text",
        "css",
    ]


class Navigation(DraftModel):
    kind: Literal["navigation"] = "navigation"
    url: str


class Click(DraftModel):
    kind: Literal["click"] = "click"
    target: Target


class Fill(DraftModel):
    kind: Literal["fill"] = "fill"
    target: Target
    value: str = Field(max_length=10000)


class Press(DraftModel):
    kind: Literal["press"] = "press"
    target: Target
    key: str = Field(min_length=1, max_length=80)


DIAGNOSTICS = {
    **MESSAGES,
    "sensitive_field": "Sensitive field detected. Capture stopped; its value was refused.",
    "sensitive_value": "Potential secret detected. Capture stopped; the value was refused.",
    "unsafe_url": "A URL with credentials or sensitive parameters was refused.",
    "blocked_request": "An undeclared website dependency was blocked. Review allowed domains.",
    "blocked_navigation": "Navigation outside the declared website boundary was blocked.",
    "write_request": "A non-read-only network request was blocked.",
    "duplicate_tab": "An additional tab was closed. Only the original tab is recorded.",
    "frame": "A frame was detected. Frame interactions are unsupported and are not recorded.",
    "shadow_dom": "Shadow DOM interactions are unsupported.",
    "ambiguous_target": "No unique, stable target was found. This interaction was not captured.",
    "unsupported_input": "Unsupported input control. Only ordinary text fills are captured.",
    "unsupported_key": "This key or shortcut is unsupported and was not captured.",
    "modified_click": "Modified or non-primary clicks are unsupported.",
    "unsupported_gesture": "This gesture is unsupported and cannot be replayed from this draft.",
    "file_transfer": "Uploads and downloads are unsupported and were refused.",
    "permission_request": "A browser capability was requested. No permission was granted.",
    "dialog": "A browser dialog was dismissed; dialog interactions are unsupported.",
    "websocket": "A WebSocket connection was blocked; live socket interactions are unsupported.",
    "browser_closed": "The browser or original tab was closed before recording finished.",
    "browser_error": "The browser could not complete capture. Raw browser details were omitted.",
    "capture_error": "An event could not be captured safely. The recording is incomplete.",
    "limit": "The recording size limit was reached. Capture stopped.",
}


class Diagnostic(DraftModel):
    kind: Literal["diagnostic"] = "diagnostic"
    code: str
    message: str
    host: str | None = None


Event = Annotated[Navigation | Click | Fill | Press | Diagnostic, Field(discriminator="kind")]


class RecordingDraft(DraftModel):
    format: Literal["browser-to-mcp-recording"] = "browser-to-mcp-recording"
    version: Literal[1] = 1
    status: Literal["captured", "partial"]
    requires_review: Literal[True] = True
    allowed_domains: list[str]
    events: list[Event]


def validate_start(url: str, domains: list[str]) -> list[str]:
    try:
        normalized = Permissions(allowed_domains=domains).allowed_domains
    except ValueError:
        raise RecordingError("Provide explicit hostnames using --allow-domain.") from None
    if not safe_url(url) or not _is_allowed_url(url, normalized):
        raise RecordingError(
            "Starting URL must be public HTTP(S), without secrets, on an allowed host."
        )
    return list(dict.fromkeys(normalized))


def validate_destination(path: Path, workflow_dir: Path = Path("workflows")) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.name.endswith(DRAFT_SUFFIX):
        raise RecordingError(f"Draft destination must end with {DRAFT_SUFFIX}.")
    if resolved.is_relative_to(workflow_dir.resolve()):
        raise RecordingError("Save drafts outside the directory scanned by the MCP server.")
    if path.is_symlink() or resolved.exists():
        raise RecordingError("Draft destination already exists; choose a new filename.")
    if not resolved.parent.is_dir():
        raise RecordingError("Create the draft destination directory before recording.")
    return resolved


def save_draft(draft: RecordingDraft, path: Path, workflow_dir: Path = Path("workflows")) -> Path:
    """Atomically publish a mode-0600 file without replacing any existing destination."""
    destination = validate_destination(path, workflow_dir)
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".recording-", dir=destination.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(draft.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(
            temporary, destination
        )  # Exclusive even if another process creates the destination.
    except OSError:
        raise RecordingError(
            "Could not save the draft; no existing file was overwritten."
        ) from None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return destination
