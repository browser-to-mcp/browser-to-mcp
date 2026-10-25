"""State, idempotency, question races, redaction, and retention without website traffic."""

import asyncio
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from browser_to_mcp.jobs.api import dispatch
from browser_to_mcp.jobs.models import Budgets, JobError, JobSnapshot
from browser_to_mcp.jobs.store import RETENTION_SECONDS, JobStore
from browser_to_mcp.jobs.worker import execute_phase


def start(store, candidate, **budgets):
    return store.start(str(uuid4()), candidate, Budgets(**budgets))


def test_prepare_review_complete_and_exact_immutable_candidate(store, candidate):
    job = start(store, candidate)
    assert job["state"] == "queued" and job["stage"] == "prepare"
    assert store.claim()["id"] == job["id"]
    asyncio.run(execute_phase(store, job["id"]))
    ready = store.read(job["id"])
    JobSnapshot.model_validate(ready)
    assert ready["state"] == "ready_for_review"
    assert ready["usage"]["browser_actions"] == ready["usage"]["model_requests"] == 0
    assert ready["question"]["choices"] == ["keep", "discard"]
    artifact = store.artifact_path(job["id"], "draft")
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == ready["artifact"]["sha256"]
    queued = store.answer(job["id"], ready["question"]["id"], ready["revision"], "keep")
    assert queued["state"] == "queued" and queued["stage"] == "finish"
    assert queued["question"] is None
    store.claim()
    asyncio.run(execute_phase(store, job["id"]))
    completed = store.read(job["id"])
    assert completed["state"] == "completed" and completed["artifact"] == ready["artifact"]
    assert store.cancel(job["id"]) == completed
    store.maintain()
    assert not store.artifact_path(job["id"], "input").exists()
    assert artifact.exists()
    assert not list(store.directory.glob("*.yaml"))


def test_polling_is_read_only_even_after_deadline_and_without_worker(tmp_path, candidate):
    clock = [1000.0]
    store = JobStore(tmp_path / "jobs", clock=lambda: clock[0])
    job = start(store, candidate, wall_seconds=1)
    before = store.database.read_bytes(), store.database.stat().st_mtime_ns
    clock[0] += 100
    for _ in range(20):
        assert store.read(job["id"]) == job
    assert before == (store.database.read_bytes(), store.database.stat().st_mtime_ns)
    store.maintain()
    failed = store.read(job["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "budget_wall_seconds"


def test_missing_status_read_creates_no_directory(store):
    with pytest.raises(JobError, match="does not exist"):
        store.read(str(uuid4()))
    assert not store.directory.exists()


def test_repeated_and_concurrent_start_requests_have_one_identity(store, candidate):
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: store.start(key, candidate, Budgets()), range(8)))
    assert len({job["id"] for job in jobs}) == 1
    assert len(list(store.directory.glob("*.input.candidate"))) == 1
    first = jobs[0]
    store.cancel(first["id"])
    assert store.start(key, candidate, Budgets())["state"] == "cancelled"
    with pytest.raises(JobError) as caught:
        store.start(key, candidate, Budgets(wall_seconds=10))
    assert caught.value.code == "conflict"


def test_questions_validate_choices_and_reject_duplicate_stale_answers(store, candidate):
    job = start(store, candidate)
    store.claim()
    question = store.ask(job["id"], "output_shape", "prepare")
    qid, revision = question["question"]["id"], question["revision"]
    for value in ("PRIVATE-PASSWORD", True, {"password": "secret"}):
        with pytest.raises(JobError) as caught:
            store.answer(job["id"], qid, revision, value)
        assert caught.value.code == "invalid_answer" and "PRIVATE" not in str(caught.value)
    with pytest.raises(JobError) as caught:
        store.answer(job["id"], str(uuid4()), revision, "list")
    assert caught.value.code == "stale_answer"
    with ThreadPoolExecutor(max_workers=2) as pool:

        def answer(_):
            try:
                return store.answer(job["id"], qid, revision, "list")["state"]
            except JobError as error:
                return error.code

        assert sorted(pool.map(answer, range(2))) == ["queued", "stale_answer"]
    store.claim()
    second = store.ask(job["id"], "output_shape", "prepare")
    assert second["question"]["id"] != qid
    with pytest.raises(JobError):
        store.answer(job["id"], qid, second["revision"], "single")
    assert "PRIVATE" not in store.database.read_bytes().decode(errors="ignore")


def test_waiting_time_counts_and_expired_answer_never_resumes(tmp_path, candidate):
    clock = [10.0]
    store = JobStore(tmp_path / "jobs", clock=lambda: clock[0])
    job = start(store, candidate, wall_seconds=1)
    store.claim()
    waiting = store.ask(job["id"], "output_shape", "prepare")
    clock[0] = 11.0
    with pytest.raises(JobError) as caught:
        store.answer(job["id"], waiting["question"]["id"], waiting["revision"], "list")
    assert caught.value.code == "budget_wall_seconds"
    assert store.read(job["id"])["state"] == "failed"
    assert store.claim() is None


@pytest.mark.parametrize("state", ["queued", "running", "waiting_for_input", "ready_for_review"])
def test_cancel_transitions_are_idempotent(store, candidate, state):
    job = start(store, candidate)
    if state != "queued":
        store.claim()
    if state == "waiting_for_input":
        store.ask(job["id"], "output_shape", "prepare")
    if state == "ready_for_review":
        asyncio.run(execute_phase(store, job["id"]))
    result = store.cancel(job["id"])
    assert result["cancel_requested"]
    assert result["state"] == ("running" if state == "running" else "cancelled")
    assert store.cancel(job["id"]) == result
    if state == "running":
        with pytest.raises(JobError) as caught:
            store.reserve(job["id"], browser_actions=0)
        assert caught.value.code == "cancelled"
        store.finish_failure(job["id"], "interrupted")
        assert store.read(job["id"])["state"] == "cancelled"
    store.maintain()
    assert not list(store.directory.glob("*.candidate"))


def test_cancel_wins_before_completion_and_completed_is_not_reopened(store, candidate):
    job = start(store, candidate)
    store.claim()
    asyncio.run(execute_phase(store, job["id"]))
    ready = store.read(job["id"])
    store.answer(job["id"], ready["question"]["id"], ready["revision"], "keep")
    store.claim()
    store.cancel(job["id"])
    asyncio.run(execute_phase(store, job["id"]))
    assert store.read(job["id"])["state"] == "cancelled"


@pytest.mark.parametrize(
    "dimension", ["browser_actions", "model_requests", "tokens", "cost_microusd", "output_bytes"]
)
def test_budget_reservations_are_atomic_and_cannot_exceed_limits(store, candidate, dimension):
    job = start(store, candidate, **{dimension: 2})
    store.claim()
    store.reserve(job["id"], **{dimension: 2})
    with pytest.raises(JobError) as caught:
        store.reserve(job["id"], **{dimension: 1})
    assert caught.value.code == "budget_" + dimension
    assert store.read(job["id"])["usage"][dimension] == 2
    with pytest.raises(JobError):
        store.reserve(job["id"], **{dimension: -1})
    with pytest.raises(JobError):
        store.reserve(job["id"], **{dimension: True})


def test_output_budget_failure_and_tampered_reviewed_artifact(store, candidate):
    job = start(store, candidate, output_bytes=1)
    store.claim()
    asyncio.run(execute_phase(store, job["id"]))
    assert store.read(job["id"])["error"]["code"] == "budget_output_bytes"
    job = start(store, candidate)
    store.claim()
    asyncio.run(execute_phase(store, job["id"]))
    ready = store.read(job["id"])
    store.artifact_path(job["id"], "draft").write_text("changed after review")
    store.answer(job["id"], ready["question"]["id"], ready["revision"], "keep")
    store.claim()
    asyncio.run(execute_phase(store, job["id"]))
    assert store.read(job["id"])["error"]["code"] == "invalid_candidate"


def test_recovery_fails_running_work_and_never_requeues_it(store, candidate):
    running = start(store, candidate)
    store.claim()
    queued = start(store, candidate)
    store.recover()
    failed = store.read(running["id"])
    assert failed["state"] == "failed" and failed["error"]["code"] == "interrupted"
    assert store.read(queued["id"])["state"] == "queued"
    assert store.claim()["id"] == queued["id"]


def test_retention_purges_data_but_request_tombstone_prevents_restart(tmp_path, candidate):
    clock = [1.0]
    store = JobStore(tmp_path / "jobs", clock=lambda: clock[0])
    key = str(uuid4())
    job = store.start(key, candidate, Budgets())
    store.cancel(job["id"])
    clock[0] += RETENTION_SECONDS + 1
    store.maintain()
    with pytest.raises(JobError) as caught:
        store.start(key, candidate, Budgets())
    assert caught.value.code == "expired_key"
    assert not list(store.directory.glob("*.candidate"))
    clock[0] += 10 * RETENTION_SECONDS
    store.maintain()
    with pytest.raises(JobError) as caught:
        store.start(key, candidate, Budgets())
    assert caught.value.code == "expired_key"


@pytest.mark.parametrize("bad", [True, -1, 1.5, "2", None])
def test_invalid_budgets_rejected_before_store_creation(store, candidate, bad):
    with pytest.raises(JobError) as caught:
        dispatch(
            store,
            "start",
            dict(request_key=str(uuid4()), candidate=candidate, budgets={"wall_seconds": bad}),
        )
    assert caught.value.code == "invalid_request"
    assert not store.directory.exists()


@pytest.mark.parametrize("source", ["password: PRIVATE-SECRET", "a: &a [*a]", "x" * 262145])
def test_bad_or_secret_candidate_is_not_persisted(store, source):
    with pytest.raises(JobError) as caught:
        start(store, source)
    assert caught.value.code == "invalid_candidate"
    assert "PRIVATE" not in str(caught.value)
    assert not store.directory.exists()


def test_metadata_has_no_source_paths_values_or_raw_request_key(store, candidate):
    key = str(uuid4())
    source = candidate.replace("Search Hacker News", "PRIVATE-DESCRIPTION")
    job = store.start(key, source, Budgets())
    status = json.dumps(store.read(job["id"]))
    assert "PRIVATE" not in status and str(Path.cwd()) not in status and key not in status
    assert "PRIVATE" not in store.database.read_bytes().decode(errors="ignore")
    assert store.database.stat().st_mode & 0o077 == 0
    assert store.artifact_path(job["id"], "input").stat().st_mode & 0o077 == 0


def test_unknown_store_schema_and_invalid_job_ids_are_safe(store):
    store.initialize()
    with sqlite3.connect(store.database) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(JobError) as caught:
        store.read(str(uuid4()))
    assert caught.value.code == "store_unavailable"
    with pytest.raises(ValidationError):
        Budgets(tokens=1000001)


def test_input_snapshot_tampering_fails_instead_of_changing_the_requested_job(store, candidate):
    job = start(store, candidate)
    store.artifact_path(job["id"], "input").write_text(
        candidate.replace("hn_search", "changed_name")
    )
    store.claim()
    asyncio.run(execute_phase(store, job["id"]))
    assert store.read(job["id"])["error"]["code"] == "invalid_candidate"
    assert not store.artifact_path(job["id"], "draft").exists()


def test_cleanup_only_removes_owned_artifact_names(store):
    store.initialize()
    other = store.directory / "my-notes.tmp"
    other.write_text("keep this file")
    unknown = store.directory / "my-work.candidate"
    unknown.write_text("keep this too")
    orphan = store.artifact_path(str(uuid4()), "input")
    orphan.write_text("orphaned job input")
    store.maintain()
    assert other.read_text() == "keep this file" and unknown.read_text() == "keep this too"
    assert not orphan.exists()


@pytest.mark.parametrize("limit", ["MAX_JOBS", "MAX_REQUESTS"])
def test_capacity_failure_does_not_write_another_input(store, candidate, monkeypatch, limit):
    monkeypatch.setattr("browser_to_mcp.jobs.store." + limit, 1)
    start(store, candidate)
    with pytest.raises(JobError) as caught:
        start(store, candidate)
    assert caught.value.code == "capacity"
    assert len(list(store.directory.glob("*.input.candidate"))) == 1


def test_public_job_view_stays_bounded(store, candidate):
    job = start(store, candidate)
    store.claim()
    asyncio.run(execute_phase(store, job["id"]))
    public = json.dumps(store.read(job["id"]))
    assert len(public.encode()) < 16384
    assert "source_sha256" not in public and "resume_phase" not in public
