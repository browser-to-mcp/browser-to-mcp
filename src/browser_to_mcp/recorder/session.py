"""Own one fresh Chromium context and the recording boundary for its entire lifetime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from importlib.resources import files

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from browser_to_mcp.recorder.capture import Capture
from browser_to_mcp.recorder.draft import RecordingDraft, safe_url, validate_start
from browser_to_mcp.replay import _is_allowed_url


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
            self.context = await self.browser.new_context(
                accept_downloads=False,
                service_workers="block",
                permissions=[],
            )
            await self.context.route("**/*", self._route)
            await self.context.route_web_socket("**/*", self._socket)
            await self.context.expose_binding("__btmCapture", self._event)
            script = (
                files("browser_to_mcp.recorder").joinpath("assets/capture.js").read_text("utf-8")
            )
            await self.context.add_init_script(script=script)
            self.page = await self.context.new_page()
            self.context.on("page", self._extra_page)
            self.page.on("close", self._closed)
            self.page.on("crash", self._closed)
            self.page.on("framenavigated", self._navigation)
            self.page.on("frameattached", lambda _: self.capture.diagnose("frame"))
            self.page.on("download", self._download)
            self.page.on("dialog", self._dialog)
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
        self.capture.accept(payload)
        if self.capture.halted:
            self.ended.set()

    async def _route(self, route) -> None:
        try:
            await self._enforce_route(route)
        except PlaywrightError:
            if not self._closing:
                self.capture.diagnose("capture_error")
            with suppress(PlaywrightError):
                await route.abort()

    async def _enforce_route(self, route) -> None:
        request = route.request
        url = request.url
        navigation = request.is_navigation_request()
        if not _is_allowed_url(url, self.capture.domains):
            self.capture.diagnose(
                "blocked_navigation" if navigation else "blocked_request",
                request_url=url,
            )
            await route.abort()
        elif not safe_url(url):
            self.capture.diagnose("unsafe_url")
            await route.abort()
        elif request.method not in {"GET", "HEAD", "OPTIONS"}:
            self.capture.diagnose("write_request")
            await route.abort()
        else:
            if navigation and self.page is not None:
                try:
                    frame = request.frame
                except PlaywrightError:
                    # Popup navigation can arrive before its frame or page exists.
                    self.capture.diagnose("duplicate_tab")
                    await route.abort()
                    return
                if frame != self.page.main_frame:
                    code = "frame" if frame.page == self.page else "duplicate_tab"
                    self.capture.diagnose(code)
                    await route.abort()
                    return
            await route.continue_()

    async def _socket(self, socket) -> None:
        self.capture.diagnose("websocket")
        with suppress(PlaywrightError):
            await socket.close()

    async def _extra_page(self, page: Page) -> None:
        self.capture.diagnose("duplicate_tab")
        with suppress(PlaywrightError):
            await page.close()

    def _navigation(self, frame) -> None:
        if self._paused or self._closing or frame != self.page.main_frame:
            return
        if not _is_allowed_url(frame.url, self.capture.domains):
            self.capture.diagnose("blocked_navigation")
            return
        self.capture.accept({"kind": "navigation", "url": frame.url})
        if self.capture.halted:
            self.ended.set()

    async def _download(self, download) -> None:
        self.capture.diagnose("file_transfer")
        with suppress(PlaywrightError):
            await download.cancel()

    async def _dialog(self, dialog) -> None:
        self.capture.diagnose("dialog")
        with suppress(PlaywrightError):
            await dialog.dismiss()

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
