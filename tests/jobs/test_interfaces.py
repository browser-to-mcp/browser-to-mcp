"""Actual STDIO reconnects and CLI commands share persistent job state and errors."""

import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.cli import run
from browser_to_mcp.jobs.models import Budgets


def params(store):
    return StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "from browser_to_mcp.cli import main; main()",
            "jobs",
            "--directory",
            str(store.directory),
            "server",
        ],
        env={k: os.environ[k] for k in ["PLAYWRIGHT_BROWSERS_PATH"] if k in os.environ},
    )


def worker_once(store):
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
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_cli_and_reconnected_mcp_clients_follow_one_job(store, candidate, tmp_path, capsys):
    source = tmp_path / "source.yaml"
    source.write_text(candidate)
    key = str(uuid4())
    base = ["jobs", "--directory", str(store.directory)]
    assert run(base + ["start", str(source), "--request-key", key]) == 0
    initial = json.loads(capsys.readouterr().out)
    job_id = initial["id"]

    async def first_connection():
        async with stdio_client(params(store)) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                listing = await client.list_tools()
                assert {tool.name for tool in listing.tools} == {
                    "start_build_job",
                    "read_build_job",
                    "answer_build_job",
                    "cancel_build_job",
                }
                read_tool = next(t for t in listing.tools if t.name == "read_build_job")
                assert read_tool.annotations.readOnlyHint
                for _ in range(3):
                    result = await client.call_tool("read_build_job", {"job_id": job_id})
                    assert not result.isError and result.structuredContent == initial
                repeated = await client.call_tool(
                    "start_build_job",
                    {
                        "request_key": key,
                        "candidate": candidate,
                    },
                )
                assert repeated.structuredContent == initial

    asyncio.run(first_connection())
    assert store.read(job_id) == initial  # closing MCP did not start/cancel the job
    worker_once(store)
    assert run(base + ["read", job_id]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["state"] == "ready_for_review"

    arguments = dict(
        job_id=job_id,
        question_id=ready["question"]["id"],
        revision=ready["revision"],
        value="PRIVATE-PASSWORD",
    )
    assert (
        run(
            base
            + [
                "answer",
                job_id,
                "--question-id",
                arguments["question_id"],
                "--revision",
                str(arguments["revision"]),
                "--value",
                arguments["value"],
            ]
        )
        == 2
    )
    cli_error = json.loads(capsys.readouterr().err)
    assert "PRIVATE" not in json.dumps(cli_error)

    async def second_connection():
        async with stdio_client(params(store)) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                current = await client.call_tool("read_build_job", {"job_id": job_id})
                assert current.structuredContent == ready
                bad = await client.call_tool("answer_build_job", arguments)
                assert bad.isError and bad.structuredContent == cli_error
                good = await client.call_tool("answer_build_job", {**arguments, "value": "keep"})
                assert not good.isError and good.structuredContent["state"] == "queued"
                duplicate = await client.call_tool(
                    "answer_build_job", {**arguments, "value": "keep"}
                )
                assert duplicate.isError and duplicate.structuredContent["code"] == "stale_answer"

    asyncio.run(second_connection())
    worker_once(store)
    assert run(base + ["read", job_id]) == 0
    complete = json.loads(capsys.readouterr().out)
    assert complete["state"] == "completed"

    async def third_connection():
        async with stdio_client(params(store)) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                repeated = await client.call_tool(
                    "start_build_job",
                    {
                        "request_key": key,
                        "candidate": candidate,
                    },
                )
                assert repeated.structuredContent == complete
                cancelled = await client.call_tool("cancel_build_job", {"job_id": job_id})
                assert cancelled.structuredContent == complete

    asyncio.run(third_connection())
    assert len(list(store.directory.glob("*.draft.candidate"))) == 1
    assert not list(store.directory.glob("*.yaml"))


def test_mcp_and_cli_reject_invalid_limits_with_same_error(store, candidate, tmp_path, capsys):
    path = tmp_path / "candidate.yaml"
    path.write_text(candidate)
    key = str(uuid4())
    assert (
        run(
            [
                "jobs",
                "--directory",
                str(store.directory),
                "start",
                str(path),
                "--request-key",
                key,
                "--tokens",
                "-1",
            ]
        )
        == 2
    )
    expected = json.loads(capsys.readouterr().err)

    async def invoke():
        async with stdio_client(params(store)) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                result = await client.call_tool(
                    "start_build_job",
                    {
                        "request_key": key,
                        "candidate": candidate,
                        "budgets": {"tokens": -1},
                    },
                )
                assert result.isError and result.structuredContent == expected

    asyncio.run(invoke())
    assert not store.directory.exists()


def test_mcp_can_cancel_queued_job_without_starting_worker(store, candidate):
    job = store.start(str(uuid4()), candidate, Budgets())

    async def invoke():
        async with stdio_client(params(store)) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                result = await client.call_tool("cancel_build_job", {"job_id": job["id"]})
                assert not result.isError and result.structuredContent["state"] == "cancelled"
                same = await client.call_tool("cancel_build_job", {"job_id": job["id"]})
                assert result.structuredContent == same.structuredContent

    asyncio.run(invoke())
    assert store.claim() is None
