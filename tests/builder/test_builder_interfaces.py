import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

from builder_site import model_server, proposal, task
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.cli import run


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
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "PRIVATE-KEY" not in result.stdout + result.stderr


def test_real_cli_worker_and_mcp_share_task_job(store, catalog_site, tmp_path, monkeypatch, capsys):
    _, base = catalog_site
    request = task(base)
    path = tmp_path / "task.json"
    path.write_text(json.dumps(request))
    key = str(uuid4())
    cli = ["jobs", "--directory", str(store.directory)]
    assert (
        run(
            cli
            + [
                "start-task",
                str(path),
                "--request-key",
                key,
                "--browser-actions",
                "4",
                "--model-requests",
                "1",
                "--tokens",
                "10000",
            ]
        )
        == 0
    )
    initial = json.loads(capsys.readouterr().out)
    with model_server(proposal()) as (model, endpoint):
        monkeypatch.setenv("BROWSER_TO_MCP_MODEL_URL", endpoint)
        monkeypatch.setenv("BROWSER_TO_MCP_MODEL_API_KEY", "PRIVATE-KEY")
        worker_once(store)
        ready = store.read(initial["id"])
        assert ready["state"] == "ready_for_review", ready
        assert ready["report"]["provider"] == "llama_cpp"
        assert ready["report"]["prompt_tokens"] == 100
        assert ready["usage"]["tokens"] == 4196
        assert "PRIVATE-KEY" not in json.dumps(model.calls)
        assert model.headers[-1]["Authorization"] == "Bearer PRIVATE-KEY"
        assert [name for name, _ in model.calls] == ["/apply-template", "/tokenize", "/completion"]

        async def reconnect():
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-c",
                    "from browser_to_mcp.cli import main; main()",
                    *cli,
                    "server",
                ],
                env={
                    name: os.environ[name]
                    for name in ["PLAYWRIGHT_BROWSERS_PATH"]
                    if name in os.environ
                },
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    repeated = await client.call_tool(
                        "start_task_build_job",
                        dict(
                            request_key=key,
                            task=request,
                            budgets=dict(browser_actions=4, model_requests=1, tokens=10000),
                        ),
                    )
                    assert repeated.structuredContent == ready
                    bad = await client.call_tool(
                        "start_task_build_job",
                        dict(
                            request_key=str(uuid4()),
                            task=request | {"credentials": "PRIVATE"},
                        ),
                    )
                    assert bad.isError
                    assert bad.structuredContent["code"] == "invalid_task"
                    answer = await client.call_tool(
                        "answer_build_job",
                        dict(
                            job_id=ready["id"],
                            question_id=ready["question"]["id"],
                            revision=ready["revision"],
                            value="keep",
                        ),
                    )
                    assert answer.structuredContent["state"] == "queued"

        asyncio.run(reconnect())
        worker_once(store)
        assert store.read(ready["id"])["state"] == "completed"
        assert len(model.calls) == 3
    store.maintain()
    assert not list(store.directory.glob("*.input.candidate"))
    assert not list(store.directory.glob("*.yaml"))
    assert "PRIVATE" not in store.database.read_bytes().decode(errors="replace")


def test_cli_rejects_bad_task_files_before_storage(store, tmp_path, capsys):
    path = tmp_path / "task.json"
    for data in [b"[]", b"{}", b"not json", b"\xff", b"x" * 262145]:
        path.write_bytes(data)
        assert (
            run(
                [
                    "jobs",
                    "--directory",
                    str(store.directory),
                    "start-task",
                    str(path),
                    "--request-key",
                    str(uuid4()),
                ]
            )
            == 2
        )
        assert json.loads(capsys.readouterr().err)["code"] == "invalid_task"
        assert not store.directory.exists()


def test_real_worker_cancels_inflight_model_and_does_not_replay(
    store,
    catalog_site,
    monkeypatch,
):
    import threading

    from browser_to_mcp.jobs.models import Budgets

    _, base = catalog_site
    initial = store.start_task(
        str(uuid4()),
        task(base),
        Budgets(browser_actions=4, model_requests=1, tokens=10000),
    )
    with model_server(proposal(), gate=threading.Event()) as (model, endpoint):
        monkeypatch.setenv("BROWSER_TO_MCP_MODEL_URL", endpoint)
        owner = subprocess.Popen(
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert model.completion_started.wait(15)
            assert store.read(initial["id"])["stage"] == "ask_model"
            store.cancel(initial["id"])
            stdout, stderr = owner.communicate(timeout=10)
            assert owner.returncode == 0, stderr
            assert stdout == stderr == ""
            result = store.read(initial["id"])
            assert result["state"] == "cancelled" and result["artifact"] is None
            assert result["usage"]["model_requests"] == 1
            assert not list(store.directory.glob("*.candidate"))
            worker_once(store)
            assert [path for path, _ in model.calls].count("/completion") == 1
        finally:
            if owner.poll() is None:
                owner.terminate()
                owner.communicate(timeout=10)
