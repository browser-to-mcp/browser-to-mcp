"""Interactive terminal controls for recording, explicit partial saves, and cancellation."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

from browser_to_mcp.recorder.draft import (
    RecordingError,
    save_draft,
    validate_destination,
    validate_start,
)
from browser_to_mcp.recorder.session import RecordingSession

NOTICE = (
    "Demonstrate only public, non-sensitive, read-only tasks. Your actions affect the live site; "
    "the recorder is not a universal side-effect detector. Do not log in or enter secrets. "
    "Only navigation, ordinary text fills, primary clicks, and supported key presses in one "
    "tab are captured. No cookies, network bodies, DOM dumps, or screenshots are saved.\n"
    "Commands: stop = finish and save; cancel = discard; save-partial = explicitly save "
    "an incomplete draft. Ctrl+C or end of input discards the recording."
)


async def read_command(*, preserve_case: bool = False) -> str:
    """A daemon reader allows browser-close/cancellation to finish without waiting for stdin."""
    loop = asyncio.get_running_loop()
    result = loop.create_future()
    try:
        descriptor = sys.stdin.fileno()
    except (OSError, ValueError):
        descriptor = None

    def deliver(value: str) -> None:
        if not result.done():
            result.set_result(value.strip() if preserve_case else value.strip().lower())

    def read() -> None:
        try:
            if descriptor is None:
                value = sys.stdin.readline()
            else:
                # Do not hold Python's buffered stdin lock in a daemon thread at exit.
                data = bytearray()
                while len(data) < 256:
                    char = os.read(descriptor, 1)
                    if not char:
                        break
                    data.extend(char)
                    if char == b"\n":
                        break
                value = data.decode("utf-8", errors="replace")
        except (OSError, ValueError):
            value = ""
        try:
            loop.call_soon_threadsafe(deliver, value or "cancel")
        except RuntimeError:
            pass  # The process already ended after cancellation.

    threading.Thread(target=read, daemon=True).start()
    return await result


async def record(
    url: str,
    domains: list[str],
    destination: Path,
    workflow_dir: Path,
    *,
    reader: Callable[[], Awaitable[str]] = read_command,
    session_factory=RecordingSession,
) -> int:
    validate_start(url, domains)
    validate_destination(destination, workflow_dir)
    print(NOTICE, file=sys.stderr, flush=True)
    pending_command: asyncio.Task | None = None
    ended: asyncio.Task | None = None
    try:
        async with session_factory(
            url,
            domains,
            notify=lambda message: print(message, file=sys.stderr, flush=True),
        ) as session:
            ended = asyncio.create_task(session.ended.wait())
            while True:
                if pending_command is None:
                    pending_command = asyncio.create_task(reader())
                done, _ = await asyncio.wait(
                    {pending_command, ended},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if ended in done:
                    await session.close()
                    print(
                        "Recording ended. Type save-partial to keep it, or cancel to discard.",
                        file=sys.stderr,
                        flush=True,
                    )
                    # Reuse the same stdin read, rather than leaving two competing readers.
                    command = await pending_command
                else:
                    command = pending_command.result()
                pending_command = None
                if command == "cancel":
                    print(json.dumps({"status": "cancelled", "saved": False}))
                    return 1
                if command not in {"stop", "save-partial"}:
                    print("Type stop, cancel, or save-partial.", file=sys.stderr, flush=True)
                    continue
                draft = await session.finish(partial=command == "save-partial")
                if draft.status == "partial" and command != "save-partial":
                    print(
                        "This recording is incomplete. Type save-partial to save it, or cancel.",
                        file=sys.stderr,
                        flush=True,
                    )
                    # Resources are closed now; preserve the in-memory draft until a choice.
                    while True:
                        command = await reader()
                        if command == "cancel":
                            print(json.dumps({"status": "cancelled", "saved": False}))
                            return 1
                        if command == "save-partial":
                            break
                        print("Type save-partial or cancel.", file=sys.stderr, flush=True)
                path = save_draft(draft, destination, workflow_dir)
                print(
                    json.dumps(
                        {
                            "status": draft.status,
                            "draft_path": str(path),
                            "requires_review": True,
                            "events": len(draft.events),
                        }
                    )
                )
                return 0
    finally:
        for task in (pending_command, ended):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (pending_command, ended) if task is not None),
            return_exceptions=True,
        )


def run_record(url: str, domains: list[str], destination: Path, workflow_dir: Path) -> int:
    try:
        return asyncio.run(record(url, domains, destination, workflow_dir))
    except KeyboardInterrupt:
        print(json.dumps({"status": "cancelled", "saved": False}))
        return 130
    except RecordingError as error:
        print(
            json.dumps({"status": "failed", "category": "recording", "message": str(error)}),
            file=sys.stderr,
        )
        return 2
