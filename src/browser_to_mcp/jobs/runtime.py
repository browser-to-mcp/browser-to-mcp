"""Meter trusted worker operations and own every resource for one job phase."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

from playwright.async_api import async_playwright

from browser_to_mcp.browser_policy import BrowserBoundary
from browser_to_mcp.records import cancellable_page_operation

from .candidate import check_candidate
from .models import JobError


class JobContext:
    def __init__(self, store, job_id):
        self.store, self.job_id = store, job_id
        self.resources = AsyncExitStack()
        self._browser = None

    async def __aenter__(self):
        await self.resources.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.resources.__aexit__(*args)

    async def browser(self):
        if self._browser is not None:
            self.store.reserve(self.job_id, browser_actions=0)
            return self._browser
        # Startup consumes an action too; the immutable request owns the host list.
        self.store.reserve(self.job_id, browser_actions=1)
        loaded, _ = check_candidate(self.store.read_artifact(self.job_id, "input").decode("utf-8"))
        playwright = await self.resources.enter_async_context(async_playwright())
        browser = await playwright.chromium.launch(headless=True)
        self.resources.push_async_callback(browser.close)
        boundary = BrowserBoundary(loaded.workflow.permissions.allowed_domains)
        context = await boundary.create_context(browser)

        async def close():
            boundary.closing = True
            await context.close()

        self.resources.push_async_callback(close)
        page = await context.new_page()
        self._browser = JobBrowser(self, page, boundary)
        return self._browser

    async def model_request(self, request, *, token_limit: int, cost_microusd: int):
        """Reserve the provider's upper bounds before dispatch; never refund a reservation.

        A future provider must enforce token_limit and supply a conservative cost bound.
        Credentials stay in the provider, never in job records or arguments to this service.
        """
        if type(token_limit) is not int or token_limit <= 0:
            raise JobError("invalid_request")
        self.store.reserve(
            self.job_id, model_requests=1, tokens=token_limit, cost_microusd=cost_microusd
        )
        result = await request(token_limit=token_limit)
        if not isinstance(result, str):
            raise JobError("worker_failed")
        self.store.reserve(self.job_id, output_bytes=len(result.encode()))
        return result

    async def process(self, *command):
        """Own an internal helper, never a command taken from a job request."""
        self.store.reserve(self.job_id, browser_actions=0)  # state/deadline/cancellation check
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def close():
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 3)
                except TimeoutError:
                    process.kill()
                    await process.wait()

        self.resources.push_async_callback(close)
        return process


class JobBrowser:
    """Small metered browser interface for trusted builders; no page-supplied code."""

    def __init__(self, context, page, boundary):
        self._context, self._page, self._boundary = context, page, boundary

    async def _call(self, operation):
        self._context.store.reserve(self._context.job_id, browser_actions=1)
        if self._boundary.failure:
            raise JobError("worker_failed")
        result = await cancellable_page_operation(self._page, operation())
        if self._boundary.failure:
            raise JobError("worker_failed")
        return result

    async def goto(self, url):
        response = await self._call(
            lambda: self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        )
        if response is None or not 200 <= response.status < 300:
            raise JobError("worker_failed")

    async def text(self, selector):
        text = await self._call(lambda: self._page.locator(selector).inner_text(timeout=5000))
        self._context.store.reserve(self._context.job_id, output_bytes=len(text.encode()))
        return text
