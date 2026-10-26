"""One foreground owner, isolated phase processes, and no automatic phase retries."""

from __future__ import annotations

import asyncio
import fcntl
import os
import signal
import sys
from contextlib import contextmanager, suppress
from pathlib import Path

from browser_to_mcp.compiler import compile_workflow

from .candidate import check_candidate
from .models import JobError, State
from .runtime import JobContext
from .store import JobStore


@contextmanager
def worker_lock(store):
    store.initialize()
    path = store.directory / "worker.lock"
    if path.is_symlink():
        raise JobError("store_unavailable")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JobError("worker_busy") from None
        yield
    finally:
        os.close(fd)


async def prepare_phase(context, job):
    store, job_id = context.store, context.job_id
    if job["kind"] == "generate_workflow" and job["phase"] == "generate":
        from browser_to_mcp.builder.service import generate_phase

        return await generate_phase(context, job)
    if job["kind"] == "generate_workflow" and job["phase"] == "check_proposal":
        from browser_to_mcp.builder.service import check_client_proposal

        return await check_client_proposal(context, job)
    if job["phase"] == "prepare":
        loaded, data = check_candidate(store.read_artifact(job_id, "input").decode("utf-8"))
        compile_workflow(loaded)
        store.save_draft(job_id, data, loaded.manifest_digest)
        return "review"
    if job["phase"] == "finish" and job["answer"] and job["answer"]["value"] == "keep":
        return "complete"
    raise JobError("worker_failed")


async def execute_phase(store, job_id, handler=prepare_phase):
    """A handler closes resources before committing waiting or terminal state."""
    try:
        job = store.internal(job_id)
        async with JobContext(store, job_id) as context:
            outcome = await handler(context, job)
        if outcome == "review":
            store.ask(job_id, "review_draft", "finish", ready=True)
        elif outcome == "proposal":
            store.ask(job_id, "draft_proposal", "check_proposal")
        elif outcome == "complete":
            store.complete(job_id)
        elif isinstance(outcome, tuple) and len(outcome) == 2:
            store.ask(job_id, outcome[0], outcome[1])
        else:
            raise JobError("worker_failed")
    except asyncio.CancelledError:
        raise
    except JobError as error:
        store.finish_failure(job_id, error.code)
    except Exception:
        store.finish_failure(job_id, "worker_failed")


async def _stop_process(process):
    if process.returncode is None:
        if process.stdin:
            process.stdin.close()  # child observes EOF and cleans up its resource stack
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            pass
    # A killed phase leader cannot clean up its own helpers. Reap the whole group.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    await process.wait()


async def work(store, stop, *, once=False, child_command=None):
    """Use a local lock to serialize owners; each claimed phase runs at most once."""
    with worker_lock(store):
        store.recover()
        store.maintain()
        next_maintenance = asyncio.get_running_loop().time() + 1
        while not stop.is_set():
            if asyncio.get_running_loop().time() >= next_maintenance:
                store.maintain()
                next_maintenance = asyncio.get_running_loop().time() + 1
            job = store.claim()
            if job is None:
                if once:
                    return
                try:
                    await asyncio.wait_for(stop.wait(), 0.2)
                except TimeoutError:
                    pass
                continue
            process = None
            failure_code = "worker_failed"
            try:
                command = child_command or [sys.executable, "-m", "browser_to_mcp.jobs.worker"]
                process = await asyncio.create_subprocess_exec(
                    *command,
                    str(store.directory),
                    job["id"],
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    start_new_session=True,
                )
                while process.returncode is None:
                    current = store.internal(job["id"])
                    expired = store.clock() >= current["deadline"]
                    if stop.is_set() or current["cancel_requested"] or expired:
                        await _stop_process(process)
                        store.finish_failure(
                            job["id"],
                            "cancelled"
                            if current["cancel_requested"]
                            else "budget_wall_seconds"
                            if expired
                            else "interrupted",
                            only_running=True,
                        )
                        break
                    try:
                        await asyncio.wait_for(process.wait(), 0.05)
                    except TimeoutError:
                        pass
            except asyncio.CancelledError:
                failure_code = "interrupted"
                raise
            except Exception:
                # A failed phase launch must not expose raw process arguments or retry work.
                pass
            finally:
                if process is not None:
                    await _stop_process(process)
                current = store.internal(job["id"])
                if current["state"] == State.RUNNING:
                    store.finish_failure(job["id"], failure_code)
                store.maintain()
            if once:
                return


async def child(store, job_id, handler=prepare_phase):
    """Parent-pipe EOF is a shutdown signal even if the worker was killed abruptly."""
    if os.getpgrp() != os.getpid():
        raise JobError("invalid_request")
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    reader = asyncio.StreamReader()
    transport, _ = await loop.connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )
    parent_gone = asyncio.create_task(reader.read(1))
    stopped = asyncio.create_task(stop.wait())
    job = store.internal(job_id)
    timer = asyncio.create_task(asyncio.sleep(max(0, job["deadline"] - store.clock())))
    task = asyncio.create_task(execute_phase(store, job_id, handler))
    try:
        done, _ = await asyncio.wait(
            [task, parent_gone, stopped, timer], return_when=asyncio.FIRST_COMPLETED
        )
        if task not in done:
            timed_out = timer in done or store.clock() >= job["deadline"]
            task.cancel()
            _, pending = await asyncio.wait([task], timeout=4)
            if pending:
                # A stuck helper must not outlive this phase. Recovery records the interruption.
                os.killpg(os.getpgrp(), signal.SIGKILL)
            await asyncio.gather(task, return_exceptions=True)
            code = "budget_wall_seconds" if timed_out else "interrupted"
            store.finish_failure(job_id, code)
        else:
            await task
    finally:
        for pending in (parent_gone, stopped, timer):
            pending.cancel()
        await asyncio.gather(parent_gone, stopped, timer, return_exceptions=True)
        transport.close()
        store.maintain()


def run_worker(directory, *, once=False):
    async def run():
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        await work(JobStore(directory), stop, once=once)

    asyncio.run(run())


if __name__ == "__main__":
    asyncio.run(child(JobStore(Path(sys.argv[1])), sys.argv[2]))
