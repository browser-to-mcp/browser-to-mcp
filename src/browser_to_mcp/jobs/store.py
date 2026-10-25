"""Transactional local job state. Reading a job never schedules or repairs work."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import UUID, uuid4

from .candidate import MAX_SOURCE_BYTES, check_candidate
from .models import (
    MESSAGES,
    QUESTION_TEXT,
    TERMINAL,
    TRANSITIONS,
    Budgets,
    BuildReport,
    JobError,
    JobSnapshot,
    State,
    Usage,
)

RETENTION_SECONDS = 7 * 86400
MAX_JOBS = 1000
MAX_REQUESTS = 10000


def _digest(value: bytes):
    return hashlib.sha256(value).hexdigest()


def _identifier(value):
    try:
        parsed = UUID(value)
        if str(parsed) != value or parsed.version != 4:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise JobError("invalid_request") from None
    return value


class JobStore:
    def __init__(self, directory: Path, *, clock=time.time):
        self.directory = Path(directory).expanduser().absolute()
        self.database = self.directory / "jobs.sqlite3"
        self.clock = clock
        self._initialized = False

    def initialize(self):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
                raise JobError("store_unavailable")
            try:
                fd = os.open(self.database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
            if self.database.is_symlink() or self.database.stat().st_mode & 0o077:
                raise JobError("store_unavailable")
            with sqlite3.connect(self.database, timeout=5) as db:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise JobError("store_unavailable")
                db.execute(
                    "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, "
                    "state TEXT NOT NULL, updated REAL NOT NULL, data TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS requests (key_hash TEXT PRIMARY KEY, "
                    "request_hash TEXT NOT NULL, job_id TEXT NOT NULL)"
                )
                db.execute("PRAGMA user_version=1")
            self._initialized = True
        except (OSError, sqlite3.Error):
            raise JobError("store_unavailable") from None

    @contextmanager
    def _db(self, *, write=False):
        db = None
        try:
            if self.database.is_symlink():
                raise JobError("store_unavailable")
            if write and not self._initialized:
                self.initialize()
            elif not self.database.is_file():
                raise JobError("not_found")
            db = sqlite3.connect(
                f"file:{quote(str(self.database))}?mode={'rw' if write else 'ro'}",
                uri=True,
                timeout=5,
            )
            db.row_factory = sqlite3.Row
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise JobError("store_unavailable")
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
            try:
                yield db
            except JobError:
                # Expected errors may follow an intentional expiry transition.
                if write:
                    db.commit()
                raise
            else:
                if write:
                    db.commit()
        except (OSError, sqlite3.Error, json.JSONDecodeError):
            raise JobError("store_unavailable") from None
        finally:
            if db is not None:
                db.close()

    def _load(self, db, job_id):
        _identifier(job_id)
        row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise JobError("not_found")
        return json.loads(row["data"])

    def _save(self, db, job):
        job["revision"] += 1
        job["updated_at"] = self.clock()
        db.execute(
            "UPDATE jobs SET state=?, updated=?, data=? WHERE id=?",
            (job["state"], job["updated_at"], json.dumps(job), job["id"]),
        )

    def _transition(self, job, state, error=None):
        if state not in TRANSITIONS[job["state"]]:
            raise JobError("invalid_transition")
        job["state"] = state
        job["error"] = {"code": error, "message": MESSAGES[error]} if error else None
        if state in TERMINAL:
            job["question"] = None
            job["answer"] = None
            job["resume_phase"] = None
            if state != State.COMPLETED:
                job["artifact"] = None

    def _public(self, job):
        public = {
            key: value
            for key, value in job.items()
            if key not in {"phase", "resume_phase", "answer", "source_sha256", "progress"}
        }
        public["stage"] = job.get("progress", job["phase"])
        return JobSnapshot.model_validate(public).model_dump(mode="json")

    def read(self, job_id):
        with self._db() as db:
            return self._public(self._load(db, job_id))

    def internal(self, job_id):
        with self._db() as db:
            return self._load(db, job_id)

    def artifact_path(self, job_id, kind):
        _identifier(job_id)
        if kind not in {"input", "draft"}:
            raise JobError("invalid_request")
        return self.directory / f"{job_id}.{kind}.candidate"

    def _write_artifact(self, path, data):
        temporary = self.directory / (uuid4().hex + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def read_artifact(self, job_id, kind):
        job = self.internal(job_id)
        path = self.artifact_path(job_id, kind)
        if path.is_symlink() or not path.is_file():
            raise JobError("invalid_candidate")
        limit = MAX_SOURCE_BYTES if kind == "input" else job["budgets"]["output_bytes"]
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        expected = (
            job["source_sha256"]
            if kind == "input"
            else (job["artifact"]["sha256"] if job["artifact"] else None)
        )
        if len(data) > limit or _digest(data) != expected:
            raise JobError("invalid_candidate")
        return data

    def start(self, request_key: str, source: str, budgets: Budgets):
        _, data = check_candidate(source)
        return self._start(request_key, data, budgets, kind="prepare_workflow", phase="prepare")

    def start_task(self, request_key: str, task, budgets: Budgets):
        from browser_to_mcp.builder.contracts import check_task

        _, data = check_task(task)
        return self._start(request_key, data, budgets, kind="generate_workflow", phase="generate")

    def _start(self, request_key, data, budgets, *, kind, phase):
        _identifier(request_key)
        if not isinstance(budgets, Budgets):
            raise JobError("invalid_request")
        # Preserve existing prepare-job receipts, and separate new task requests by kind.
        identity = data if kind == "prepare_workflow" else kind.encode() + b"\n" + data
        request_hash = _digest(identity + json.dumps(budgets.model_dump(), sort_keys=True).encode())
        key_hash = _digest(request_key.encode())
        with self._db(write=True) as db:
            previous = db.execute("SELECT * FROM requests WHERE key_hash=?", (key_hash,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise JobError("conflict")
                try:
                    return self._public(self._load(db, previous["job_id"]))
                except JobError as error:
                    if error.code == "not_found":
                        raise JobError("expired_key") from None
                    raise
            if (
                db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= MAX_JOBS
                or db.execute("SELECT count(*) FROM requests").fetchone()[0] >= MAX_REQUESTS
            ):
                raise JobError("capacity")
            now = self.clock()
            job = dict(
                id=str(uuid4()),
                kind=kind,
                state=State.QUEUED,
                revision=1,
                created_at=now,
                updated_at=now,
                deadline=now + budgets.wall_seconds,
                budgets=budgets.model_dump(),
                usage=Usage().model_dump(),
                phase=phase,
                question=None,
                answer=None,
                resume_phase=None,
                cancel_requested=False,
                error=None,
                artifact=None,
                source_sha256=_digest(data),
            )
            self._write_artifact(self.artifact_path(job["id"], "input"), data)
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?)", (job["id"], job["state"], now, json.dumps(job))
            )
            db.execute(
                "INSERT INTO requests VALUES (?,?,?)",
                (key_hash, request_hash, job["id"]),
            )
            return self._public(job)

    def record_report(self, job_id, report: BuildReport):
        with self._db(write=True) as db:
            job = self._running(db, job_id)
            job["report"] = BuildReport.model_validate(report).model_dump()
            self._save(db, job)

    def set_stage(self, job_id, stage):
        if stage not in {"inspect_page", "ask_model", "check_draft"}:
            raise JobError("invalid_request")
        with self._db(write=True) as db:
            job = self._running(db, job_id)
            job["progress"] = stage
            self._save(db, job)

    def _running(self, db, job_id):
        job = self._load(db, job_id)
        if job["cancel_requested"]:
            raise JobError("cancelled")
        if job["state"] != State.RUNNING:
            raise JobError("invalid_transition")
        if self.clock() >= job["deadline"]:
            raise JobError("budget_wall_seconds")
        return job

    def claim(self):
        with self._db(write=True) as db:
            row = db.execute(
                "SELECT id FROM jobs WHERE state=? ORDER BY updated,id LIMIT 1", (State.QUEUED,)
            ).fetchone()
            if row is None:
                return None
            job = self._load(db, row["id"])
            if self.clock() >= job["deadline"]:
                self._transition(job, State.FAILED, "budget_wall_seconds")
                self._save(db, job)
                return None
            self._transition(job, State.RUNNING)
            self._save(db, job)
            return job

    def reserve(self, job_id, **amounts):
        if not amounts or any(
            key not in Usage.model_fields or type(amount) is not int or amount < 0
            for key, amount in amounts.items()
        ):
            raise JobError("invalid_request")
        with self._db(write=True) as db:
            job = self._running(db, job_id)
            for key, amount in amounts.items():
                if job["usage"][key] + amount > job["budgets"][key]:
                    raise JobError("budget_" + key)
            for key, amount in amounts.items():
                job["usage"][key] += amount
            self._save(db, job)

    def ask(self, job_id, kind, resume_phase, *, ready=False):
        if kind not in QUESTION_TEXT or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", resume_phase):
            raise JobError("invalid_request")
        if ready and kind != "review_draft":
            raise JobError("invalid_request")
        with self._db(write=True) as db:
            job = self._running(db, job_id)
            prompt, choices = QUESTION_TEXT[kind]
            job["question"] = dict(id=str(uuid4()), kind=kind, prompt=prompt, choices=choices)
            job.pop("progress", None)
            job["resume_phase"] = resume_phase
            job["answer"] = None
            self._transition(job, State.REVIEW if ready else State.WAITING)
            self._save(db, job)
            return self._public(job)

    def answer(self, job_id, question_id, revision, value):
        with self._db(write=True) as db:
            job = self._load(db, job_id)
            question = job["question"]
            if (
                type(revision) is not int
                or revision != job["revision"]
                or not question
                or question_id != question["id"]
                or job["state"] not in {State.WAITING, State.REVIEW}
            ):
                raise JobError("stale_answer")
            if not isinstance(value, str) or value not in question["choices"]:
                raise JobError("invalid_answer")
            if self.clock() >= job["deadline"]:
                self._transition(job, State.FAILED, "budget_wall_seconds")
                self._save(db, job)
                raise JobError("budget_wall_seconds")
            if question["kind"] == "review_draft" and value == "discard":
                self._transition(job, State.CANCELLED, "cancelled")
            else:
                job["answer"] = {"question_id": question_id, "value": value}
                job["phase"] = job["resume_phase"]
                job["resume_phase"] = None
                job["question"] = None
                self._transition(job, State.QUEUED)
            self._save(db, job)
            return self._public(job)

    def cancel(self, job_id):
        with self._db(write=True) as db:
            job = self._load(db, job_id)
            if job["state"] in TERMINAL or job["cancel_requested"]:
                return self._public(job)
            job["cancel_requested"] = True
            if job["state"] != State.RUNNING:
                self._transition(job, State.CANCELLED, "cancelled")
            self._save(db, job)
            return self._public(job)

    def save_draft(self, job_id, data, manifest_digest):
        self.reserve(job_id, output_bytes=len(data))
        with self._db(write=True) as db:
            job = self._running(db, job_id)
            path = self.artifact_path(job_id, "draft")
            self._write_artifact(path, data)
            job["artifact"] = dict(
                filename=path.name, sha256=_digest(data), manifest_digest=manifest_digest
            )
            self._save(db, job)

    def complete(self, job_id):
        data = self.read_artifact(job_id, "draft")
        with self._db(write=True) as db:
            job = self._running(db, job_id)
            artifact = job["artifact"]
            if not artifact:
                raise JobError("invalid_transition")
            if _digest(data) != artifact["sha256"]:
                raise JobError("invalid_candidate")
            self._transition(job, State.COMPLETED)
            self._save(db, job)

    def finish_failure(self, job_id, code, *, only_running=False):
        if code not in MESSAGES:
            code = "worker_failed"
        with self._db(write=True) as db:
            job = self._load(db, job_id)
            if job["state"] in TERMINAL or only_running and job["state"] != State.RUNNING:
                return
            cancelled = job["cancel_requested"] or code == "cancelled"
            self._transition(
                job,
                State.CANCELLED if cancelled else State.FAILED,
                "cancelled" if cancelled else code,
            )
            self._save(db, job)

    def recover(self):
        """Call only while holding the worker lock; never replay interrupted phases."""
        with self._db(write=True) as db:
            for row in db.execute("SELECT id FROM jobs WHERE state=?", (State.RUNNING,)).fetchall():
                job = self._load(db, row["id"])
                cancelled = job["cancel_requested"]
                self._transition(
                    job,
                    State.CANCELLED if cancelled else State.FAILED,
                    "cancelled" if cancelled else "interrupted",
                )
                self._save(db, job)

    def maintain(self):
        """Explicit worker/cleanup operation. Polling does not expire or delete jobs."""
        now = self.clock()
        with self._db(write=True) as db:
            jobs = [json.loads(row["data"]) for row in db.execute("SELECT data FROM jobs")]
            for job in jobs:
                state = job["state"]
                if state not in TERMINAL and state != State.RUNNING and now >= job["deadline"]:
                    self._transition(job, State.FAILED, "budget_wall_seconds")
                    self._save(db, job)
                    state = job["state"]
                if state in TERMINAL and now - job["updated_at"] >= RETENTION_SECONDS:
                    db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
                if state in TERMINAL or state == State.REVIEW:
                    self.artifact_path(job["id"], "input").unlink(missing_ok=True)
                if state in {State.FAILED, State.CANCELLED} or (
                    state in TERMINAL and now - job["updated_at"] >= RETENTION_SECONDS
                ):
                    self.artifact_path(job["id"], "draft").unlink(missing_ok=True)
            known = {row[0] for row in db.execute("SELECT id FROM jobs")}
            for path in self.directory.iterdir():
                if re.fullmatch(r"[0-9a-f]{32}\.tmp", path.name):
                    path.unlink(missing_ok=True)
                    continue
                parts = path.name.split(".")
                if len(parts) != 3 or parts[1:] not in (
                    ["input", "candidate"],
                    ["draft", "candidate"],
                ):
                    continue
                try:
                    _identifier(parts[0])
                except JobError:
                    continue
                if parts[0] not in known:
                    path.unlink(missing_ok=True)
