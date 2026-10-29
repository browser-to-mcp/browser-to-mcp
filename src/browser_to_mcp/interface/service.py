"""Persistent UI state and bounded async operations; no alternate workflow engine."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from browser_to_mcp.builder.provider import provider_from_environment
from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.evaluation.models import Suite
from browser_to_mcp.evaluation.service import digest, evaluate
from browser_to_mcp.jobs.api import dispatch
from browser_to_mcp.jobs.candidate import check_candidate
from browser_to_mcp.jobs.models import JobError, State
from browser_to_mcp.jobs.store import JobStore
from browser_to_mcp.jobs.worker import work, worker_lock
from browser_to_mcp.library.store import Library
from browser_to_mcp.replay import replay_workflow


class InterfaceError(ValueError):
    pass


def identifier(value):
    from uuid import UUID

    if not isinstance(value, str) or UUID(value).hex != value.replace("-", ""):
        raise InterfaceError("Invalid identifier.")
    return value


class Interface:
    def __init__(self, directory: Path, jobs: Path, library: Path, *, start_worker=True):
        self._owner = self._loop = self._thread = self._worker = None
        self._closed = False
        try:
            self._initialize(directory, jobs, library, start_worker=start_worker)
        except BaseException:
            self.close()
            raise

    def _initialize(self, directory, jobs, library, *, start_worker):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
            raise InterfaceError("Choose a private interface directory (mode 0700).")
        self._owner = os.open(self.directory / "owner.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self._owner)
            self._owner = None
            raise InterfaceError("Another interface owns this directory.") from None
        self.jobs = JobStore(jobs)
        self.jobs.initialize()
        self.library = Library(library)
        self.database = self.directory / "interface.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE, request_hash TEXT,
                    kind TEXT, state TEXT, result TEXT, error TEXT);
                CREATE TABLE IF NOT EXISTS imports (
                    job TEXT PRIMARY KEY, tool TEXT, version INTEGER);
            """)
            db.execute(
                "UPDATE operations SET state='interrupted',error='Interrupted; "
                "explicitly start a new attempt.' WHERE state IN ('queued','running')"
            )
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve_loop, daemon=True)
        self._thread.start()
        self._ready.wait()
        self._futures = {}
        self._lock = threading.RLock()
        self._worker = None
        if start_worker:
            # Fail early if a different owner already runs this directory's worker.
            with worker_lock(self.jobs):
                pass
            self._worker = asyncio.run_coroutine_threadsafe(
                self._start_worker(), self._loop
            ).result()

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _serve_loop(self):
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        self._loop.close()

    async def _start_worker(self):
        self._worker_stop = asyncio.Event()
        return asyncio.create_task(work(self.jobs, self._worker_stop))

    def close(self):
        if self._closed:
            return
        self._closed = True

        async def finish():
            if self._worker:
                self._worker_stop.set()
                await asyncio.gather(self._worker, return_exceptions=True)
            tasks = [t for t in asyncio.all_tasks() if t != asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        try:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(finish(), self._loop).result(timeout=20)
        finally:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=5)
            if self._owner is not None:
                os.close(self._owner)
                self._owner = None

    def status(self):
        with self.jobs._db() as db:
            jobs = [
                self.jobs.read(row["id"])
                for row in db.execute("SELECT id FROM jobs ORDER BY updated DESC LIMIT 100")
            ]
        with self.db() as db:
            operations = [
                dict(row)
                for row in db.execute(
                    "SELECT id,kind,state,result,error FROM operations ORDER BY rowid "
                    "DESC LIMIT 100"
                )
            ]
        for operation in operations:
            operation["result"] = json.loads(operation["result"]) if operation["result"] else None
        try:
            provider_from_environment()
            provider_ready = True
        except JobError:
            provider_ready = False
        return {
            "jobs": jobs,
            "tools": self.library.list(),
            "operations": operations,
            "provider_ready": provider_ready,
            "worker_ready": bool(self._worker and not self._worker.done()),
            "jobs_directory": str(self.jobs.directory),
            "executable": str(Path(sys.executable).with_name("browser-to-mcp")),
            "library_directory": str(self.library.directory),
        }

    def operation(self, key):
        identifier(key)
        with self.db() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (key,)).fetchone()
        if not row:
            raise InterfaceError("Unknown operation.")
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def _start(self, kind, args, action):
        key = identifier(args["request_key"])
        hashed = digest(json.dumps(args, sort_keys=True, allow_nan=False).encode())
        with self._lock, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM operations WHERE request_key=?", (key,)).fetchone()
            if previous:
                if previous["request_hash"] != hashed or previous["kind"] != kind:
                    raise InterfaceError("Request key already belongs to a different operation.")
                return {"operation_id": previous["id"]}
            if (
                db.execute(
                    "SELECT count(*) FROM operations WHERE state IN ('queued','running')"
                ).fetchone()[0]
                >= 2
            ):
                raise InterfaceError("Two operations are already active. Wait or cancel one.")
            operation = uuid4().hex
            db.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,?,?)",
                (operation, key, hashed, kind, "queued", None, None),
            )
            db.commit()
            self._futures[operation] = asyncio.run_coroutine_threadsafe(
                self._execute(operation, action), self._loop
            )
            self._futures[operation].add_done_callback(lambda _: self._forget(operation))
        return {"operation_id": operation}

    def _forget(self, key):
        with self._lock:
            self._futures.pop(key, None)

    async def _execute(self, key, action):
        try:
            with self.db() as db:
                db.execute(
                    "UPDATE operations SET state='running' WHERE id=? AND state='queued'", (key,)
                )
            result = await action(key)
            with self.db() as db:
                db.execute(
                    "UPDATE operations SET state='completed',result=? WHERE id=? AND "
                    "state!='cancelled'",
                    (json.dumps(result, allow_nan=False), key),
                )
        except asyncio.CancelledError:
            with self.db() as db:
                db.execute(
                    "UPDATE operations SET state='cancelled',error='Cancelled; no "
                    "automatic retry.' WHERE id=?",
                    (key,),
                )
            raise
        except Exception:
            with self.db() as db:
                db.execute(
                    "UPDATE operations SET state='failed',error='The operation "
                    "failed. Inspect the plan, inputs, permissions and retained "
                    "evidence; correct and start a new attempt.' WHERE id=? AND state!='cancelled'",
                    (key,),
                )

    def _import(self, args):
        job = self.jobs.read(args["job_id"])
        if job["state"] != State.COMPLETED:
            raise InterfaceError(
                "Keep the checked draft before importing it for tests and publication."
            )
        with self.db() as db:
            prior = db.execute("SELECT * FROM imports WHERE job=?", (job["id"],)).fetchone()
            if prior:
                return self.library.review(prior["tool"], prior["version"])
            data = self.jobs.read_artifact(job["id"], "draft")
            target = self.directory / (job["id"] + ".candidate")
            if target.exists():
                if target.read_bytes() != data:
                    raise InterfaceError("The saved candidate changed.")
            else:
                target.write_bytes(data)
            record = self.library.import_draft(
                target, origin="build job " + job["id"], tool_id=args.get("tool_id")
            )
            db.execute(
                "INSERT INTO imports VALUES(?,?,?)",
                (job["id"], record["tool"]["id"], record["version"]["version"]),
            )
            return record

    def _candidate(self, tool, version, root):
        review = self.library.review(tool, version)
        with self.library.connection() as db:
            source = self.library._version(db, tool, version)["source"]
        path = root / "draft.candidate"
        path.write_text(source, encoding="utf-8")
        return path, review

    def dispatch(self, operation, args):
        if operation == "status":
            return self.status()
        if operation == "start":
            if args.get("generation", "client") == "provider":
                provider_from_environment()  # Never accept provider URLs or keys from HTTP.
            return dispatch(self.jobs, "start-task", args)
        if operation in {"read", "answer", "context", "submit-proposal", "cancel"}:
            return dispatch(self.jobs, operation, args)
        if operation == "draft":
            job = self.jobs.read(args["job_id"])
            if job["state"] not in {State.REVIEW, State.COMPLETED}:
                raise InterfaceError("The draft is not ready for preview.")
            loaded, _ = check_candidate(self.jobs.read_artifact(job["id"], "draft").decode())
            compiled = compile_workflow(loaded)
            return {
                "candidate_sha256": digest(self.jobs.read_artifact(job["id"], "draft")),
                "workflow": loaded.workflow.model_dump(mode="json"),
                "input_schema": compiled.input_schema,
                "output_schema": compiled.output_schema,
                "limitations": [
                    "First-page preview is not independent test evidence.",
                    "Public read-only tasks only; no universal website reliability guarantee.",
                ],
            }
        if operation == "import":
            with self._lock:
                return self._import(args)
        if operation == "review":
            return self.library.review(args["tool_id"], args["version"])
        if operation in {"publish", "rollback"}:
            return self.library.activate(
                args["tool_id"],
                args["version"],
                revision=args["revision"],
                candidate_sha256=args["candidate_sha256"],
                approve=operation == "publish" and args.get("approve") is True,
                rollback=operation == "rollback",
            )
        if operation == "retire":
            return self.library.retire(args["tool_id"], revision=args["revision"])
        if operation == "history":
            return self.library.history(args["tool_id"])
        if operation == "operation":
            return self.operation(args["operation_id"])
        if operation == "cancel-operation":
            key = identifier(args["operation_id"])
            with self._lock, self.db() as db:
                db.execute(
                    "UPDATE operations SET state='cancelled',error='Cancelled; no "
                    "automatic retry.' WHERE id=? AND state IN ('queued','running')",
                    (key,),
                )
                future = self._futures.get(key)
                if future:
                    future.cancel()
            return self.operation(key)
        if operation == "evaluate":
            if args.get("approved_plan") is not True:
                raise InterfaceError(
                    "Review and explicitly approve the independently written expected answers."
                )
            suite = Suite.model_validate(args["suite"])
            raw = suite.model_dump_json(indent=2).encode()
            pin = digest(raw)

            async def check(key):
                root = self.directory / key
                root.mkdir(mode=0o700)
                candidate, _ = self._candidate(args["tool_id"], args["version"], root)
                plan = root / "plan.json"
                plan.write_bytes(raw)
                report = await evaluate(candidate, plan, root / "evidence", pin)
                review = self.library.attach_tests(
                    args["tool_id"], args["version"], root / "evidence", pin
                )
                return {
                    "report": report.model_dump(mode="json"),
                    "review": {"tool": review["tool"], "version": review["version"]},
                }

            return self._start("evaluate", args, check)
        if operation in {"run", "preview"}:
            # Pin the approved version before queueing; publication cannot retarget this run.
            if operation == "preview":
                job = self.jobs.read(args["job_id"])
                if job["state"] not in {State.REVIEW, State.COMPLETED}:
                    raise InterfaceError("The draft is not ready for preview.")
                source = self.jobs.read_artifact(job["id"], "draft")
                if digest(source) != args["candidate_sha256"]:
                    raise InterfaceError("Draft changed. Inspect it again before previewing.")
                loaded, _ = check_candidate(source.decode())
            else:
                loaded = next(
                    (
                        w
                        for w in self.library.published()
                        if w.publication.tool_id == args["tool_id"]
                    ),
                    None,
                )
                if loaded is None:
                    raise InterfaceError("Publish an approved version before running it.")
            values = (
                compile_workflow(loaded)
                .input_model.model_validate(args["inputs"])
                .model_dump(exclude_none=True)
            )

            async def execute(key):
                pairs = [
                    f"{name}={str(value).lower() if isinstance(value, bool) else value}"
                    for name, value in values.items()
                ]
                try:
                    result = await replay_workflow(loaded, pairs, self.directory / "traces", 60)
                    return {
                        "publication": vars_publication(loaded),
                        "outputs": result.outputs,
                        "trace_path": result.trace_path,
                        "duration_seconds": result.duration_seconds,
                    }
                except Exception as error:
                    from browser_to_mcp.replay import WorkflowExecutionError

                    if isinstance(error, WorkflowExecutionError):
                        return {
                            "publication": vars_publication(loaded),
                            "status": "failed",
                            "category": str(error.category),
                            "path": error.path,
                            "message": error.message,
                            "trace_path": error.trace_path,
                        }
                    raise

            return self._start(operation, args, execute)
        raise InterfaceError("Unknown operation.")


def vars_publication(loaded):
    from dataclasses import asdict

    return asdict(loaded.publication) if loaded.publication else None
