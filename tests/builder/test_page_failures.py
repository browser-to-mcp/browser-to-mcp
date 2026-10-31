"""Preserve safe page failure reasons across inspection and proposal checking."""

import asyncio
import json
from uuid import uuid4

import pytest
from builder_site import proposal, serve, task
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_to_mcp.jobs.models import Budgets
from browser_to_mcp.jobs.runtime import JobBrowser
from browser_to_mcp.jobs.worker import execute_phase


def run_phase(store):
    job = store.claim()
    assert job is not None
    asyncio.run(execute_phase(store, job["id"]))
    return store.read(job["id"])


def start(store, base, phase):
    job = store.start_task(str(uuid4()), task(base), Budgets(browser_actions=6))
    if phase == "check_proposal":
        run_phase(store)
        context = store.read_context(job["id"])
        store.submit_proposal(job["id"], context["question_id"], context["revision"], proposal())
    return job


@pytest.mark.parametrize("phase", ["generate", "check_proposal"])
def test_unapproved_asset_explains_failure_without_allowing_traffic(store, phase):
    with serve() as (site, base), serve() as (outside, outside_base):
        job = start(store, base, phase)
        site.outside_url = outside_base.replace("127.0.0.1", "localhost")
        site.mode = "external_asset"
        result = run_phase(store)
        assert result["id"] == job["id"]
        assert result["state"] == "failed"
        assert result["error"]["code"] == "page_blocked_request"
        assert "outside the declared" in result["error"]["message"]
        assert result["artifact"] is None and result["report"] is None
        assert result["usage"]["model_requests"] == 0
        assert outside.hits == []
        assert site.hits.count("/books/?q=notebook") == (2 if phase == "check_proposal" else 1)
        saved = json.dumps(result)
        assert "NEVER-SEND" not in saved and "localhost" not in saved


@pytest.mark.parametrize("phase", ["generate", "check_proposal"])
@pytest.mark.parametrize(
    "exception,code",
    [
        (PlaywrightTimeoutError, "page_timeout"),
        (PlaywrightError, "page_unavailable"),
    ],
)
def test_navigation_errors_do_not_expose_raw_details(
    store, catalog_site, monkeypatch, phase, exception, code
):
    _, base = catalog_site
    job = start(store, base, phase)

    async def unavailable(self, url):
        raise exception("https://PRIVATE.example/?token=NEVER-SEND")

    monkeypatch.setattr(JobBrowser, "goto", unavailable)
    result = run_phase(store)
    assert result["id"] == job["id"]
    assert result["state"] == "failed" and result["error"]["code"] == code
    assert result["artifact"] is None and result["usage"]["model_requests"] == 0
    assert "NEVER-SEND" not in json.dumps(result)
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("timing", ["before", "during", "playwright_error"])
def test_latched_policy_failure_wins_over_browser_details(monkeypatch, timing):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from browser_to_mcp.browser_policy import BrowserBoundary

    boundary = BrowserBoundary(["example.com"])
    context = SimpleNamespace(store=Mock(), job_id="test-job")
    browser = JobBrowser(context, object(), boundary)
    operation = AsyncMock(return_value="NEVER-SEND")

    async def during(page, action):
        await action
        boundary.deny("blocked_request")
        if timing == "playwright_error":
            raise PlaywrightError("https://PRIVATE.example/?token=NEVER-SEND")
        return "NEVER-SEND"

    monkeypatch.setattr("browser_to_mcp.jobs.runtime.cancellable_page_operation", during)
    if timing == "before":
        boundary.deny("blocked_request")
    from browser_to_mcp.jobs.models import JobError

    with pytest.raises(JobError) as raised:
        asyncio.run(browser._call(operation))
    assert raised.value.code == "page_blocked_request"
    assert "NEVER-SEND" not in str(raised.value) and "PRIVATE" not in str(raised.value)
    if timing == "before":
        operation.assert_not_called()
