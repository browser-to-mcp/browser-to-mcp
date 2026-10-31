"""One local browser boundary for recording, preview, and workflow execution."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from importlib.resources import files
from urllib.parse import parse_qsl, urlsplit

from playwright.async_api import Browser, BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

SECRET_NAME = re.compile(
    r"password|passwd|token|secret|credential|authorization|session|api.?key|^code$|^key$",
    re.IGNORECASE,
)
SECRET_VALUE = re.compile(r"(?:eyJ[\w-]{8,}\.[\w-]+\.[\w-]+|\bBearer\s+\S+)", re.IGNORECASE)
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MESSAGES = {
    "blocked_navigation": "Navigation outside the declared HTTP(S) hosts was blocked.",
    "blocked_request": "A request outside the declared HTTP(S) hosts was blocked.",
    "unsafe_url": "A URL with credentials or sensitive parameters was refused.",
    "write_request": "The request method is not allowed by the read-only browser policy.",
    "redirect": "HTTP redirects are unsupported. Review and use the final URL directly.",
    "duplicate_tab": "An additional tab was blocked. Only the original tab is supported.",
    "frame": "Child frames are unsupported and their requests are blocked.",
    "file_transfer": "An undeclared download or file chooser was blocked.",
    "permission_request": "A browser capability was refused. No permission was granted.",
    "dialog": "A browser dialog was dismissed; dialog interactions are unsupported.",
    "websocket": "A WebSocket connection was blocked.",
    "service_worker": "Service workers are blocked.",
    "worker": "Background workers are unsupported and were blocked.",
    "socket": "Direct browser transports are unsupported and were blocked.",
    "network_error": "A browser request could not complete. Raw network details were omitted.",
}


def is_allowed_url(url: str, domains: list[str]) -> bool:
    """HTTP(S) host matching; preserve the contract's explicit subdomain semantics."""
    try:
        parsed = urlsplit(url)
        parsed.port
        host = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme in {"http", "https"}
            and bool(host)
            and any(host == domain or host.endswith(f".{domain}") for domain in domains)
        )
    except ValueError:
        return False


def safe_url(url: str) -> bool:
    """Heuristic secret rejection without returning or logging the URL."""
    try:
        parsed = urlsplit(url)
        parsed.port
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url)
            and "\\" not in url
            and "{{" not in url
            and not SECRET_VALUE.search(url)
            and not any(SECRET_NAME.search(key) for key, _ in parse_qsl(parsed.query))
            and not SECRET_NAME.search(parsed.fragment)
        )
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class BrowserViolation:
    code: str

    @property
    def message(self) -> str:
        return f"{self.code}: {MESSAGES[self.code]}"


def request_violation(
    url: str, method: str, domains: list[str], *, navigation: bool = False
) -> BrowserViolation | None:
    """Pure decision used before a request is sent. Never retain request data."""
    if not is_allowed_url(url, domains):
        return BrowserViolation("blocked_navigation" if navigation else "blocked_request")
    if not safe_url(url):
        return BrowserViolation("unsafe_url")
    if method not in READ_METHODS:
        return BrowserViolation("write_request")
    return None


class BrowserBoundary:
    """Own policy hooks for exactly one original page in one fresh local context.

    A violation latches for the lifetime of the context, blocks later traffic,
    and makes the recording incomplete or the workflow fail. No request URLs,
    headers, bodies, dialog text, or page contents enter the diagnostic state.
    """

    def __init__(
        self,
        domains: list[str],
        notify: Callable[[str], None] | None = None,
        *,
        evaluation_page: tuple[str, str] | None = None,
    ):
        self.domains = list(domains)
        self._evaluation_page = evaluation_page
        self.failure: BrowserViolation | None = None
        self.page: Page | None = None
        self.download_active = False
        self._has_document = False
        self._notify = notify or (lambda _: None)
        self.closing = False

    def deny(self, code: str) -> None:
        if self.closing or code not in MESSAGES:
            return
        if self.failure is None:
            self.failure = BrowserViolation(code)
            self._notify(code)

    async def create_context(
        self, browser: Browser, *, environment: str = "local"
    ) -> BrowserContext:
        # This is not an SSRF boundary for hosted workers. There is no hosted mode.
        if environment != "local":
            raise ValueError("Only local execution is supported; hosted execution is unavailable.")
        context = await browser.new_context(
            accept_downloads=False,
            service_workers="block",
            permissions=[],
            java_script_enabled=self._evaluation_page is None,
        )
        try:
            await context.route("**/*", self._route)
            await context.route_web_socket("**/*", self._socket)
            await context.expose_binding("__btmPolicy", self._capability)
            await context.add_init_script(
                script=files("browser_to_mcp").joinpath("browser_policy.js").read_text("utf-8")
            )
            context.on("page", self._new_page)
            return context
        except BaseException:
            await context.close()
            raise

    def _capability(self, _source: dict, code: object) -> None:
        if isinstance(code, str):
            self.deny(code)

    async def _new_page(self, page: Page) -> None:
        if self.page is None:
            self.page = page
            page.on("frameattached", lambda _: self.deny("frame"))
            page.on("framenavigated", self._navigation)
            page.on("download", self._download)
            page.on("filechooser", lambda _: self.deny("file_transfer"))
            page.on("dialog", self._dialog)
        else:
            self.deny("duplicate_tab")
            # Context request routing also refuses the initial popup request.
            with suppress(PlaywrightError):
                await page.close()

    def _navigation(self, frame) -> None:
        if self.page is not None and frame == self.page.main_frame:
            # The initial empty document is created before any workflow navigation.
            if frame.url == "about:blank" and not self._has_document:
                return
            self._has_document = True
            violation = request_violation(frame.url, "GET", self.domains, navigation=True)
            if violation:
                self.deny(violation.code)

    async def _route(self, route) -> None:
        response = None
        try:
            if self.failure or self.closing:
                await route.abort()
                return
            request = route.request
            violation = request_violation(
                request.url,
                request.method,
                self.domains,
                navigation=request.is_navigation_request(),
            )
            if violation:
                self.deny(violation.code)
                await route.abort()
                return
            try:
                frame = request.frame
            except PlaywrightError:
                self.deny("duplicate_tab")
                await route.abort()
                return
            if self.page is None or frame != self.page.main_frame:
                self.deny("frame" if self.page and frame.page == self.page else "duplicate_tab")
                await route.abort()
                return
            if self._evaluation_page is not None:
                url, markup = self._evaluation_page
                if (
                    request.url != url
                    or request.method != "GET"
                    or not request.is_navigation_request()
                ):
                    self.deny("blocked_request")
                    await route.abort()
                else:
                    await route.fulfill(
                        status=200, content_type="text/html; charset=utf-8", body=markup
                    )
                return
            # continue_ follows redirect chains without rerouting their later hops.
            # Fetch one response only; never release a redirect to the browser.
            response = await route.fetch(max_redirects=0, max_retries=0, timeout=30000)
            if 300 <= response.status < 400 and response.headers.get("location"):
                self.deny("redirect")
                await route.abort()
            elif (
                "attachment" in response.headers.get("content-disposition", "").lower()
                and not self.download_active
            ):
                self.deny("file_transfer")
                await route.abort()
            elif self.failure or self.closing:
                await route.abort()
            else:
                await route.fulfill(response=response)
        except PlaywrightError:
            self.deny("network_error")
            with suppress(PlaywrightError):
                await route.abort()
        finally:
            if response is not None:
                with suppress(PlaywrightError):
                    await response.dispose()

    async def _socket(self, socket) -> None:
        self.deny("websocket")
        with suppress(PlaywrightError):
            await socket.close()

    async def _download(self, download) -> None:
        if not self.download_active:
            self.deny("file_transfer")
        # Even declared downloads return a filename only; do not keep file bytes.
        with suppress(PlaywrightError):
            await download.cancel()

    async def _dialog(self, dialog) -> None:
        self.deny("dialog")
        with suppress(PlaywrightError):
            await dialog.dismiss()
