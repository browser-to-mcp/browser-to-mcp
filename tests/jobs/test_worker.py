"""Real worker processes, abrupt death, browser cleanup, and budget dispatch gates."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from browser_to_mcp.jobs.models import Budgets, JobError
from browser_to_mcp.jobs.runtime import JobContext
from browser_to_mcp.jobs.worker import execute_phase, work, worker_lock


@contextmanager
def local_site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.server.hits += 1
            body = b"<html><h1>Local job fixture</h1></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.hits = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def wait_until(check, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError("Expected worker observation did not arrive")


def stopped(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not result.stdout.strip() or result.stdout.strip().startswith("Z")


def test_real_phase_process_prepares_and_completes_without_website_requests(store, candidate):
    job = store.start(str(uuid4()), candidate, Budgets())
    asyncio.run(work(store, asyncio.Event(), once=True))
    ready = store.read(job["id"])
    assert ready["state"] == "ready_for_review"
    store.answer(job["id"], ready["question"]["id"], ready["revision"], "keep")
    asyncio.run(work(store, asyncio.Event(), once=True))
    completed = store.read(job["id"])
    assert completed["state"] == "completed"
    assert completed["usage"]["browser_actions"] == completed["usage"]["model_requests"] == 0
    assert not list(store.directory.glob("*.yaml"))


def test_second_worker_is_refused_while_owner_holds_lock(store):
    with worker_lock(store):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from browser_to_mcp.cli import main; main()",
                "jobs",
                "--directory",
                str(store.directory),
                "worker",
                "--once",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert json.loads(result.stderr)["code"] == "worker_busy"


@pytest.mark.parametrize(
    "stop_kind", ["cancel", "shutdown", "owner_crash", "phase_crash", "deadline"]
)
def test_resources_close_on_all_terminal_paths_and_work_is_not_repeated(
    store, candidate, stop_kind
):
    budgets = Budgets(browser_actions=2, wall_seconds=8 if stop_kind == "deadline" else 60)
    job = store.start(str(uuid4()), candidate, budgets)
    helper = Path(__file__).with_name("worker_helper.py")
    with local_site() as (server, url):
        owner = subprocess.Popen(
            [sys.executable, str(helper), "owner", url, str(store.directory)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            wait_until(lambda: (store.directory / "ready").exists())
            processes = json.loads((store.directory / "ready").read_text())
            assert len(processes["children"]) >= 3  # helper, Playwright driver, Chromium
            assert server.hits == 1
            if stop_kind == "cancel":
                assert store.cancel(job["id"])["state"] == "running"
            elif stop_kind == "shutdown":
                owner.terminate()
            elif stop_kind == "owner_crash":
                owner.kill()
                owner.wait(timeout=10)
            elif stop_kind == "phase_crash":
                os.kill(processes["phase"], signal.SIGKILL)
            final = wait_until(
                lambda: (
                    value
                    if (value := store.read(job["id"]))["state"] in {"failed", "cancelled"}
                    else None
                )
            )
            assert final["state"] == ("cancelled" if stop_kind == "cancel" else "failed")
            if stop_kind == "deadline":
                assert final["error"]["code"] == "budget_wall_seconds"
            wait_until(lambda: stopped(processes["helper"]))
            wait_until(lambda: stopped(processes["phase"]))
            wait_until(lambda: all(stopped(pid) for pid in processes["children"]))
            if stop_kind != "phase_crash":
                assert (store.directory / "browser-closed").exists()
            assert not final["artifact"] and not list(store.directory.glob("*.yaml"))
        finally:
            if owner.poll() is None:
                owner.terminate()
            try:
                out, err = owner.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                owner.kill()
                out, err = owner.communicate(timeout=5)
            assert "Traceback" not in err, err
        asyncio.run(work(store, asyncio.Event(), once=True))
        assert server.hits == 1
        assert (store.directory / "attempts").read_text().splitlines() == [job["id"]]
        assert store.read(job["id"])["state"] in {"failed", "cancelled"}


@pytest.mark.parametrize("dimension", ["model_requests", "tokens", "cost_microusd"])
def test_model_budget_refusal_happens_before_provider_dispatch(store, candidate, dimension):
    budgets = {"model_requests": 1, "tokens": 100, "cost_microusd": 1000}
    budgets[dimension] = 0
    job = store.start(str(uuid4()), candidate, Budgets(**budgets))
    store.claim()
    calls = []

    async def handler(context, _):
        async def provider(**kwargs):
            calls.append(kwargs)
            return "draft"

        await context.model_request(provider, token_limit=100, cost_microusd=1000)

    asyncio.run(execute_phase(store, job["id"], handler))
    assert calls == []
    assert store.read(job["id"])["error"]["code"] == "budget_" + dimension


def test_model_reservation_is_conservative_and_output_is_bounded(store, candidate):
    job = store.start(
        str(uuid4()),
        candidate,
        Budgets(model_requests=1, tokens=100, cost_microusd=1000, output_bytes=3),
    )
    store.claim()
    calls = []

    async def handler(context, _):
        async def provider(**kwargs):
            calls.append(kwargs)
            return "oversized response"

        await context.model_request(provider, token_limit=100, cost_microusd=1000)

    asyncio.run(execute_phase(store, job["id"], handler))
    failed = store.read(job["id"])
    assert calls == [{"token_limit": 100}]
    assert failed["usage"]["tokens"] == 100 and failed["usage"]["cost_microusd"] == 1000
    assert failed["error"]["code"] == "budget_output_bytes"


def test_browser_action_budget_stops_before_second_request(store, candidate):
    job = store.start(str(uuid4()), candidate, Budgets(browser_actions=2))
    store.claim()
    with local_site() as (server, url):

        async def handler(context, _):
            browser = await context.browser()
            await browser.goto(url)
            await browser.goto(url)

        asyncio.run(execute_phase(store, job["id"], handler))
        assert server.hits == 1
    assert store.read(job["id"])["error"]["code"] == "budget_browser_actions"


def test_job_browser_uses_shared_domain_policy(store, candidate):
    data = yaml.safe_load(candidate)
    data["permissions"]["allowed_domains"] = ["example.com"]
    for step in data["steps"]:
        if step["action"] == "goto":
            step["url"] = "https://example.com/"
    candidate = yaml.safe_dump(data)
    job = store.start(str(uuid4()), candidate, Budgets(browser_actions=2))
    store.claim()
    with local_site() as (server, url):

        async def handler(context, _):
            browser = await context.browser()
            await browser.goto(url)

        asyncio.run(execute_phase(store, job["id"], handler))
        assert server.hits == 0
    assert store.read(job["id"])["state"] == "failed"


def test_zero_browser_budget_does_not_launch_chromium(store, candidate, monkeypatch):
    job = store.start(str(uuid4()), candidate, Budgets())
    store.claim()
    calls = []
    monkeypatch.setattr("browser_to_mcp.jobs.runtime.async_playwright", lambda: calls.append(True))

    async def check():
        async with JobContext(store, job["id"]) as context:
            with pytest.raises(JobError) as caught:
                await context.browser()
            assert caught.value.code == "budget_browser_actions"

    asyncio.run(check())
    assert calls == []


def test_phase_launch_failure_has_safe_error_and_is_not_retried(store, candidate):
    job = store.start(str(uuid4()), candidate, Budgets())
    asyncio.run(
        work(
            store, asyncio.Event(), once=True, child_command=["/missing/PRIVATE-worker-executable"]
        )
    )
    failed = store.read(job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "worker_failed"
    assert "PRIVATE" not in json.dumps(failed)
    assert store.claim() is None


@pytest.mark.parametrize("reason", ["cancelled", "expired"])
def test_stopped_job_cannot_start_browser(store, candidate, monkeypatch, reason):
    job = store.start(str(uuid4()), candidate, Budgets(browser_actions=2))
    store.claim()
    if reason == "cancelled":
        store.cancel(job["id"])
    else:
        store.clock = lambda: job["deadline"] + 1
    calls = []
    monkeypatch.setattr("browser_to_mcp.jobs.runtime.async_playwright", lambda: calls.append(True))

    async def check():
        async with JobContext(store, job["id"]) as context:
            with pytest.raises(JobError) as caught:
                await context.browser()
            assert caught.value.code == (
                "cancelled" if reason == "cancelled" else "budget_wall_seconds"
            )

    asyncio.run(check())
    assert not calls


def test_asking_question_releases_helpers_before_waiting(store, candidate):
    job = store.start(str(uuid4()), candidate, Budgets())
    store.claim()
    helpers = []

    async def ask(context, _):
        helper = await context.process(sys.executable, "-c", "import time; time.sleep(60)")
        helpers.append(helper)
        return "output_shape", "prepare"

    asyncio.run(execute_phase(store, job["id"], ask))
    assert store.read(job["id"])["state"] == "waiting_for_input"
    assert len(helpers) == 1 and helpers[0].returncode is not None


def test_shutdown_keeps_a_finished_review_phase_while_its_process_is_exiting(store, candidate):
    job = store.start(str(uuid4()), candidate, Budgets())
    command = [
        sys.executable,
        "-c",
        (
            "import asyncio,sys; from pathlib import Path; "
            "from browser_to_mcp.jobs.store import JobStore; "
            "from browser_to_mcp.jobs.worker import execute_phase; "
            "asyncio.run(execute_phase(JobStore(Path(sys.argv[1])), sys.argv[2])); "
            "sys.stdin.buffer.read()"
        ),
    ]

    async def shutdown_after_review():
        stop = asyncio.Event()
        owner = asyncio.create_task(work(store, stop, child_command=command))
        try:
            async with asyncio.timeout(10):
                while store.read(job["id"])["state"] != "ready_for_review":
                    await asyncio.sleep(0.02)
            stop.set()
            await owner
        finally:
            if not owner.done():
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)

    asyncio.run(shutdown_after_review())
    ready = store.read(job["id"])
    assert ready["state"] == "ready_for_review" and ready["artifact"]
    store.answer(job["id"], ready["question"]["id"], ready["revision"], "keep")
    asyncio.run(work(store, asyncio.Event(), once=True))
    assert store.read(job["id"])["state"] == "completed"


def test_phase_reuses_one_browser_and_charges_startup(store, candidate):
    job = store.start(str(uuid4()), candidate, Budgets(browser_actions=2))
    store.claim()
    with local_site() as (server, url):

        async def handler(context, _):
            browser = await context.browser()
            assert await context.browser() is browser
            await browser.goto(url)
            return "output_shape", "prepare"

        asyncio.run(execute_phase(store, job["id"], handler))
        assert server.hits == 1
    snapshot = store.read(job["id"])
    assert snapshot["state"] == "waiting_for_input"
    assert snapshot["usage"]["browser_actions"] == 2
