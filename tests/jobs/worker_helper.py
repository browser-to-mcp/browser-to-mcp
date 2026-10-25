"""Test-only trusted worker: owns a real browser and a child process while paused."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from browser_to_mcp.jobs.store import JobStore
from browser_to_mcp.jobs.worker import child, work


async def paused(context, job):
    directory = context.store.directory
    with (directory / "attempts").open("a") as stream:
        stream.write(job["id"] + "\n")
    helper = await context.process(sys.executable, "-c", "import time; time.sleep(60)")
    browser = await context.browser()
    browser._page.on("close", lambda: (directory / "browser-closed").touch())
    await browser.goto(sys.argv[2])
    processes = subprocess.run(
        ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True
    )
    relationships = [tuple(map(int, line.split())) for line in processes.stdout.splitlines()]
    descendants = {os.getpid()}
    while True:
        expanded = descendants | {pid for pid, parent in relationships if parent in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    descendants.remove(os.getpid())
    (directory / "ready").write_text(
        json.dumps(
            {
                "phase": os.getpid(),
                "helper": helper.pid,
                "children": sorted(descendants),
            }
        )
    )
    await asyncio.Event().wait()


async def owner():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await work(
        JobStore(Path(sys.argv[3])),
        stop,
        child_command=[sys.executable, __file__, "child", sys.argv[2]],
    )


if __name__ == "__main__":
    if sys.argv[1] == "owner":
        asyncio.run(owner())
    else:
        asyncio.run(child(JobStore(Path(sys.argv[3])), sys.argv[4], paused))
