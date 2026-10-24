"""Own one fresh Chromium context and the recording boundary for its entire lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from importlib.resources import files

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from browser_to_mcp.browser_policy import MESSAGES, BrowserBoundary
from browser_to_mcp.browser_policy import is_allowed_url as _is_allowed_url
from browser_to_mcp.recorder.capture import Capture
from browser_to_mcp.recorder.draft import RecordingDraft, validate_start


class RecordingSession:
    """The CLI uses visible Chromium; headless mode is available for integration tests."""

    def __init__(
        self,
        url: str,
        domains: list[str],
        *,
        headless: bool = False,
        notify: Callable[[str], None] | None = None,
    ):
        self.url = url
        self.capture = Capture(validate_start(url, domains), notify)
        self.boundary = BrowserBoundary(self.capture.domains, self.capture.diagnose)
        self.headless = headless
        self.ended = asyncio.Event()
        self.page: Page | None = None
        self.context: BrowserContext | None = None
        self.browser: Browser | None = None
        self._playwright: Playwright | None = None
        self._closing = False
        self._paused = False

    async def __aenter__(self) -> RecordingSession:
        try:
            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(headless=self.headless)
            self.browser.on("disconnected", self._closed)
            self.context = await self.boundary.create_context(self.browser)
            await self.context.expose_binding("__btmCapture", self._event)
            script = (
                files("browser_to_mcp.recorder").joinpath("assets/capture.js").read_text("utf-8")
            )
            await self.context.add_init_script(script=script)
            self.page = await self.context.new_page()
            self.page.on("close", self._closed)
            self.page.on("crash", self._closed)
            self.page.on("framenavigated", self._navigation)
            await self.page.goto(self.url, wait_until="domcontentloaded", timeout=30000)
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            self.capture.diagnose("browser_error")
            self.ended.set()
            await self.close()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    def _closed(self, *_args: object) -> None:
        if not self._closing:
            self.capture.diagnose("browser_closed")
            self.ended.set()

    def _event(self, source: dict, payload: object) -> None:
        if self._paused or self._closing or self.page is None:
            return
        if source["page"] != self.page:
            self.capture.diagnose("duplicate_tab")
            return
        if source["frame"] != self.page.main_frame:
            self.capture.diagnose("frame")
            return
        if not _is_allowed_url(source["frame"].url, self.capture.domains):
            self.capture.diagnose("blocked_navigation")
            return
        if isinstance(payload, dict) and payload.get("kind") == "diagnostic":
            code = payload.get("code")
            if isinstance(code, str) and code in MESSAGES:
                self.boundary.deny(code)
        self.capture.accept(payload)
        if self.capture.halted:
            self.ended.set()

    def _navigation(self, frame) -> None:
        if self._paused or self._closing or frame != self.page.main_frame:
            return
        if not _is_allowed_url(frame.url, self.capture.domains):
            self.capture.diagnose("blocked_navigation")
            return
        self.capture.accept({"kind": "navigation", "url": frame.url})
        if self.capture.halted:
            self.ended.set()

    async def finish(self, *, partial: bool = False) -> RecordingDraft:
        # Round trip through the page so previously dispatched input bindings drain first.
        if self.page is not None and not self.page.is_closed():
            try:
                await asyncio.wait_for(
                    self.page.evaluate("() => window.__btmPause?.()"),
                    timeout=3,
                )
            except Exception:
                self.capture.diagnose("browser_error")
        self._paused = True
        await self.close()
        return self.capture.draft(partial=partial)

    async def close(self) -> None:
        self._closing = True
        self.boundary.closing = True
        try:
            if self.context is not None:
                with suppress(PlaywrightError):
                    await self.context.close()
        finally:
            try:
                if self.browser is not None:
                    with suppress(PlaywrightError):
                        await self.browser.close()
            finally:
                if self._playwright is not None:
                    with suppress(PlaywrightError):
                        await self._playwright.stop()
                self.context = None
                self.browser = None
                self._playwright = None
