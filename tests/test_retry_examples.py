"""Additional retry examples through YAML, Chromium, CLI, and real MCP STDIO."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections import Counter
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.cli import run
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow
from browser_to_mcp.validation import FailureCategory, WorkflowValidationError

SOURCE = Path(__file__).parents[1] / "workflows" / "wikipedia_search.yaml"


def safety(idempotency: str = "safe") -> dict[str, object]:
    return {
        "timeout_seconds": 5,
        "risk": "read_only",
        "idempotency": idempotency,
        "retry": "none",
        "confirmation": "never",
    }


def write_contract(tmp_path: Path, contract: dict) -> Path:
    path = tmp_path / "example.yaml"
    path.write_text(yaml.safe_dump(contract, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize("retry", ["none", "one", "bounded"])
@pytest.mark.parametrize(
    ("action", "fields"),
    [
        ("goto", {"url": "https://en.wikipedia.org/"}),
        ("fill", {"selector": "#query", "value": "{{query}}"}),
        ("press", {"key": "End"}),
        ("click", {"selector": "#search"}),
        ("wait_for_text", {"text": "Ready"}),
        ("extract_text", {"selector": "#result"}),
        ("download", {"selector": "#download"}),
    ],
)
def test_retry_settings_on_each_supported_action(
    tmp_path: Path, retry: str, action: str, fields: dict
) -> None:
    contract = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    contract["permissions"]["downloads"] = action == "download"
    contract["steps"].insert(
        1,
        {
            "id": "example",
            "action": action,
            **fields,
            **safety("retryable"),
            "retry": retry,
        },
    )
    path = write_contract(tmp_path, contract)
    if retry == "none":
        assert load_workflow(path).workflow.steps[1].retry == "none"
    else:
        with pytest.raises(WorkflowValidationError) as error:
            load_workflow(path)
        assert error.value.category is FailureCategory.POLICY
        assert error.value.path == "steps.example.retry"
        assert error.value.message == "V0 does not support retries. Set retry to none."


class ExampleServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        self.hits: Counter[str] = Counter()
        super().__init__(("127.0.0.1", 0), ExampleHandler)


class ExampleHandler(BaseHTTPRequestHandler):
    server: ExampleServer

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        self.server.hits[path] += 1
        status = 200
        if path == "/form":
            body = """<!doctype html><meta charset="utf-8">
                <input id="query" aria-label="Query">
                <button id="search" type="button">Search</button>
                <div id="ready"></div><div id="result"></div>
                <div id="clicks">0</div><div id="keys">0</div>
                <script>
                const query = document.querySelector('#query');
                query.addEventListener('keydown', event => {
                    if (event.key === 'End') {
                        const keys = document.querySelector('#keys');
                        keys.textContent = String(Number(keys.textContent) + 1);
                    }
                });
                document.querySelector('#search').addEventListener('click', () => {
                    const clicks = document.querySelector('#clicks');
                    clicks.textContent = String(Number(clicks.textContent) + 1);
                    document.querySelector('#result').textContent = 'Result: ' + query.value;
                    document.querySelector('#ready').textContent = 'Results ready';
                });
                </script>"""
        elif path == "/flaky" and self.server.hits[path] == 1:
            status = 503
            body = "<html><body>Temporary failure. Another visit would succeed.</body></html>"
        else:
            body = '<html><body><div id="result">Recovered on another visit</div></body></html>'
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def example_server() -> Iterator[ExampleServer]:
    server = ExampleServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def local_contract(server: ExampleServer) -> dict:
    contract = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    contract["permissions"]["allowed_domains"] = ["127.0.0.1"]
    return contract


@pytest.mark.parametrize("entry_point", ["cli", "mcp_stdio"])
@pytest.mark.parametrize(
    ("idempotency", "query"),
    [
        ("safe", "python tools"),
        ("retryable", "C++ & tools #2 / 50%"),
        ("non_retryable", "नमस्ते 日本語 🚀"),
    ],
)
def test_multistep_examples_execute_each_step_once(
    tmp_path: Path,
    example_server: ExampleServer,
    capsys,
    entry_point: str,
    idempotency: str,
    query: str,
) -> None:
    contract = local_contract(example_server)
    policy = safety(idempotency)
    contract["steps"] = [
        {
            "id": "open",
            "action": "goto",
            "url": (f"http://127.0.0.1:{example_server.server_port}/form"),
            **policy,
        },
        {"id": "fill", "action": "fill", "selector": "#query", "value": "{{query}}", **policy},
        {"id": "key", "action": "press", "key": "End", **policy},
        {"id": "click", "action": "click", "selector": "#search", **policy},
        {"id": "wait", "action": "wait_for_text", "text": "Results ready", **policy},
        *[
            {"id": name, "action": "extract_text", "selector": f"#{name}", **policy}
            for name in ("result", "clicks", "keys")
        ],
    ]
    contract["outputs"] = {name: {"type": "string"} for name in ("result", "clicks", "keys")}
    contract["success"] = [
        {"id": name + "_present", "assertion": "output_not_empty", "field": name}
        for name in contract["outputs"]
    ]
    path = write_contract(tmp_path, contract)
    traces = tmp_path / "traces"
    if entry_point == "cli":
        assert (
            run(
                [
                    "replay",
                    str(path),
                    "--input",
                    f"query={query}",
                    "--trace-dir",
                    str(traces),
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
    else:

        async def invoke():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-c",
                    "from browser_to_mcp.cli import main; main()",
                    "server",
                    "--workflow-dir",
                    str(tmp_path),
                    "--trace-dir",
                    str(traces),
                ],
                env={
                    key: os.environ[key]
                    for key in ["PLAYWRIGHT_BROWSERS_PATH"]
                    if key in os.environ
                },
            )
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool("wikipedia_search", {"query": query})

        result = asyncio.run(invoke())
        assert not result.isError, result
        payload = result.structuredContent

    assert payload["outputs"] == {"result": "Result: " + query, "clicks": "1", "keys": "1"}
    assert example_server.hits["/form"] == 1
    trace = json.loads(Path(payload["trace_path"]).read_text(encoding="utf-8"))
    assert trace["status"] == "completed"
    assert [action["step_id"] for action in trace["actions"]] == [
        step["id"] for step in contract["steps"]
    ]
    assert all(action["status"] == "completed" for action in trace["actions"])


@pytest.mark.parametrize("idempotency", ["safe", "retryable", "non_retryable"])
def test_real_http_failure_is_not_revisited_or_followed_by_later_steps(
    tmp_path: Path, example_server: ExampleServer, idempotency: str
) -> None:
    contract = local_contract(example_server)
    base_url = f"http://127.0.0.1:{example_server.server_port}"
    policy = safety(idempotency)
    contract["steps"] = [
        {"id": "open", "action": "goto", "url": base_url + "/flaky", **policy},
        {
            "id": "results",
            "action": "extract_text",
            "selector": "#result",
            **policy,
            "timeout_seconds": 1,
        },
        {"id": "later", "action": "goto", "url": base_url + "/must-not-run", **policy},
    ]
    path = write_contract(tmp_path, contract)

    with pytest.raises(WorkflowExecutionError) as error:
        asyncio.run(replay_workflow(load_workflow(path), ["query=example"], tmp_path / "traces"))

    assert str(error.value.category) == "runtime"
    assert error.value.path == "steps.results"
    assert example_server.hits["/flaky"] == 1
    assert example_server.hits["/must-not-run"] == 0
    trace = json.loads(Path(error.value.trace_path).read_text(encoding="utf-8"))
    assert trace["status"] == "failed"
    assert [(action["step_id"], action["status"]) for action in trace["actions"]] == [
        ("open", "completed"),
        ("results", "failed"),
    ]
