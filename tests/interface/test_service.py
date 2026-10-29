import asyncio
import threading
from time import monotonic
from uuid import uuid4

import pytest
from evaluation_site import candidate, serve, suite

from browser_to_mcp.interface.service import Interface, InterfaceError
from browser_to_mcp.jobs.models import Budgets
from browser_to_mcp.library.store import LibraryError


def wait(service, key):
    deadline = monotonic() + 30
    event = threading.Event()
    while monotonic() < deadline:
        result = service.operation(key)
        if result["state"] not in {"queued", "running"}:
            return result
        event.wait(0.02)
    raise AssertionError("Operation did not finish")


def instance(tmp_path):
    return Interface(tmp_path / "ui", tmp_path / "jobs", tmp_path / "library", start_worker=False)


def test_publish_rejects_untested_and_failed_versions(tmp_path):
    with serve() as site:
        source = tmp_path / "input.candidate"
        import yaml

        source.write_text(yaml.safe_dump(candidate(site.base)))
        app = instance(tmp_path)
        try:
            review = app.library.import_draft(source, origin="test")
            args = dict(
                tool_id=review["tool"]["id"],
                version=1,
                revision=review["tool"]["revision"],
                candidate_sha256=review["version"]["sha"],
                approve=True,
            )
            with pytest.raises(LibraryError, match="passing evidence"):
                app.dispatch("publish", args)
            plan = suite(site.base)
            plan["cases"][0]["expected"]["outputs"]["products"][0]["price_text"] = "WRONG"
            attempt = app.dispatch(
                "evaluate",
                dict(
                    tool_id=args["tool_id"],
                    version=1,
                    suite=plan,
                    approved_plan=True,
                    request_key=str(uuid4()),
                ),
            )
            result = wait(app, attempt["operation_id"])
            assert result["state"] == "completed"
            assert not result["result"]["report"]["eligible"]
            current = app.library.review(args["tool_id"], 1)
            assert current["version"]["state"] == "failed"
            args["revision"] = current["tool"]["revision"]
            with pytest.raises(LibraryError, match="passing evidence"):
                app.dispatch("publish", args)
            assert not app.library.published()
        finally:
            app.close()


def test_cancel_operation_and_restart_do_not_repeat_work(tmp_path):
    app = instance(tmp_path)
    started = threading.Event()
    closed = threading.Event()

    async def long(key):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    args = {"request_key": str(uuid4())}
    attempt = app._start("test", args, long)
    assert started.wait(5)
    assert app._start("test", args, long) == attempt
    with pytest.raises(InterfaceError, match="different operation"):
        app._start("another", args, long)
    app.dispatch("cancel-operation", attempt)
    assert closed.wait(5)
    assert wait(app, attempt["operation_id"])["state"] == "cancelled"
    app.close()
    app = instance(tmp_path)
    try:
        assert app.operation(attempt["operation_id"])["state"] == "cancelled"
        assert app._start("test", args, long) == attempt
        assert app.operation(attempt["operation_id"])["state"] == "cancelled"
    finally:
        app.close()


def test_queued_build_cancellation_and_status_reads_do_not_restart(tmp_path):
    import yaml

    app = instance(tmp_path)
    try:
        job = app.jobs.start(
            str(uuid4()), yaml.safe_dump(candidate("http://127.0.0.1:1")), Budgets()
        )
        for _ in range(3):
            assert app.dispatch("status", {})["jobs"][0]["id"] == job["id"]
        app.dispatch("cancel", {"job_id": job["id"]})
        assert app.dispatch("status", {})["jobs"][0]["state"] == "cancelled"
        assert len(app.status()["jobs"]) == 1
        assert not app.status()["tools"]
    finally:
        app.close()


def test_unreviewed_plan_and_unpublished_run_refused(tmp_path):
    app = instance(tmp_path)
    try:
        with pytest.raises(InterfaceError, match="explicitly approve"):
            app.dispatch("evaluate", {"approved_plan": False})
        with pytest.raises(InterfaceError, match="Publish"):
            app.dispatch("run", {"tool_id": uuid4().hex, "inputs": {}, "request_key": str(uuid4())})
        assert app.status()["operations"] == []
    finally:
        app.close()


def test_shutdown_cancels_resources_and_next_start_marks_unfinished_records(tmp_path):
    app = instance(tmp_path)
    key = uuid4().hex
    with app.db() as db:
        db.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,?)",
            (key, str(uuid4()), "hash", "run", "running", None, None),
        )
    app.close()
    app = instance(tmp_path)
    try:
        assert app.operation(key)["state"] == "interrupted"
        assert app.operation(key)["error"]
    finally:
        app.close()
