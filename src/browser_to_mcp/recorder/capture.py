"""Validate small page events, coalesce fills, and keep safe ordered diagnostics."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

from pydantic import TypeAdapter, ValidationError

from browser_to_mcp.browser_policy import SECRET_NAME, SECRET_VALUE, safe_url
from browser_to_mcp.browser_policy import is_allowed_url as _is_allowed_url
from browser_to_mcp.recorder.draft import (
    DIAGNOSTICS,
    Diagnostic,
    Event,
    Fill,
    Navigation,
    RecordingDraft,
)

EVENT_ADAPTER = TypeAdapter(Event)
MAX_EVENTS = 2000


class Capture:
    def __init__(self, domains: list[str], notify: Callable[[str], None] | None = None):
        self.domains = domains
        self.events: list[Event] = []
        self.halted = False
        self.incomplete = False
        self._pending: Fill | None = None
        self._notify = notify or (lambda _: None)

    def diagnose(self, code: str, *, halt: bool = False, request_url: str | None = None) -> None:
        code = code if code in DIAGNOSTICS else "capture_error"
        self._pending = None
        self.incomplete = True
        host = None
        if request_url is not None:
            try:
                host = urlsplit(request_url).hostname
            except ValueError:
                pass
        event = Diagnostic(code=code, message=DIAGNOSTICS[code], host=host)
        if not self.events or self.events[-1] != event:
            if len(self.events) <= MAX_EVENTS:
                self.events.append(event)
            self._notify(event.message + (f" Host: {host}" if host else ""))
        self.halted |= halt

    def accept(self, payload: object) -> None:
        if self.halted:
            return
        if len(self.events) >= MAX_EVENTS:
            self.diagnose("limit", halt=True)
            return
        if not isinstance(payload, dict):
            self.diagnose("capture_error")
            return
        if payload.get("kind") == "boundary":
            self._pending = None
            return
        if payload.get("kind") == "diagnostic":
            code = payload.get("code")
            self.diagnose(str(code), halt=code in {"sensitive_field", "sensitive_value"})
            return
        try:
            event = EVENT_ADAPTER.validate_python(payload)
        except ValidationError:
            self.diagnose("capture_error")
            return
        if isinstance(event, Navigation):
            if not safe_url(event.url):
                self.diagnose("unsafe_url", halt=True)
                return
            if not _is_allowed_url(event.url, self.domains):
                self.diagnose("blocked_navigation", halt=True)
                return
        else:
            target = getattr(event, "target", None)
            if target and (
                SECRET_NAME.search(target.selector)
                or SECRET_VALUE.search(target.selector)
                or "{{" in target.selector
            ):
                self.diagnose("sensitive_value", halt=True)
                return
            if isinstance(event, Fill) and (
                SECRET_VALUE.search(event.value) or "{{" in event.value
            ):
                self.diagnose("sensitive_value", halt=True)
                return
        if isinstance(event, Fill) and self._pending is not None:
            if event.target == self._pending.target:
                self.events[-1] = event
                self._pending = event
                return
        self.events.append(event)
        self._pending = event if isinstance(event, Fill) else None

    def draft(self, *, partial: bool = False) -> RecordingDraft:
        return RecordingDraft(
            status="partial" if partial or self.incomplete else "captured",
            allowed_domains=self.domains.copy(),
            events=self.events.copy(),
        )
