"""Verify URL inputs received and decoded by HTTP through real Chromium replay.

Both public entry points use the production engine. No public websites or
mocked navigation are involved; the server reports its actual request target.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.cli import run
from browser_to_mcp.loader import load_workflow
from browser_to_mcp.server import create_server

ROOT = Path(__file__).parents[1]
VALUES = [
    "python tools",
    "C++",
    "C++ & tools",
    "cats&admin=true",
    "chapter#2",
    "50% off",
    "%2F",
    "%2e%2e",
    "नमस्ते 日本語 café 🚀",
    "../a/b?x=1",
    "https://evil.example/a?admin=true#changed",
    "//evil.example/path",
    r"a\b;c=d",
    "{{query}}",
]


class EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        # Split BEFORE decoding: each escaped slash remains data in its segment.
        observed = {
            "segments": [unquote(segment) for segment in parsed.path.split("/")],
            "query": parse_qs(parsed.query, keep_blank_values=True),
            "host": self.headers["Host"],
            "request_target": self.path,
        }
        body = (
            '<!doctype html><meta charset="utf-8">'
            f'<main id="observed">{html.escape(json.dumps(observed))}</main>'
            '<div id="fragment"></div>'
            '<script>document.querySelector("#fragment").textContent = location.hash;</script>'
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture(scope="module")
def echo_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def write_workflow(tmp_path: Path, url: str) -> Path:
    contract = yaml.safe_load((ROOT / "workflows" / "hn_search.yaml").read_text())
    contract["permissions"]["allowed_domains"] = ["127.0.0.1"]
    safety = {
        "timeout_seconds": 10,
        "risk": "read_only",
        "idempotency": "safe",
        "retry": "none",
        "confirmation": "never",
    }
    contract["steps"] = [
        {"id": "search", "action": "goto", "url": url, **safety},
        {"id": "results", "action": "extract_text", "selector": "#observed", **safety},
        {"id": "fragment", "action": "extract_text", "selector": "#fragment", **safety},
    ]
    contract["outputs"]["fragment"] = {"type": "string"}
    path = tmp_path / "echo.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    return path


@pytest.mark.parametrize("entry_point", ["cli", "mcp"])
@pytest.mark.parametrize("value", VALUES)
def test_server_receives_exact_input_and_fixed_url_structure(
    tmp_path: Path, echo_server: str, capsys, entry_point: str, value: str
) -> None:
    path = write_workflow(
        tmp_path,
        echo_server + "/lookup/{{query}}/details?q={{query}}&q=again-{{ query }}"
        "&literal=%25{{query}}&fixed=a%2Bb&fixed=two&blank=#fixed",
    )
    if entry_point == "cli":
        assert (
            run(
                [
                    "replay",
                    str(path),
                    "--input",
                    f"query={value}",
                    "--trace-dir",
                    str(tmp_path / "traces"),
                ]
            )
            == 0
        )
        outputs = json.loads(capsys.readouterr().out)["outputs"]
    else:
        server = create_server([load_workflow(path)], trace_dir=tmp_path / "traces")
        result = asyncio.run(server.call_tool("hn_search", {"query": value}))
        assert not result.isError, result
        outputs = result.structuredContent["outputs"]

    observed = json.loads(outputs["results"])
    assert observed["query"] == {
        "q": [value, f"again-{value}"],
        "literal": [f"%{value}"],
        "fixed": ["a+b", "two"],
        "blank": [""],
    }
    assert observed["segments"] == ["", "lookup", value, "details"]
    assert observed["host"] == urlsplit(echo_server).netloc
    assert "#" not in observed["request_target"]
    assert outputs["fragment"] == "#fixed"


def test_url_encoding_through_real_mcp_stdio(tmp_path: Path, echo_server: str) -> None:
    write_workflow(tmp_path, echo_server + "/lookup/{{query}}/details?q={{query}}#fixed")
    value = "C++ & tools / 日本語 #2 %2F"

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
                str(tmp_path / "traces"),
            ],
            env={key: os.environ[key] for key in ["PLAYWRIGHT_BROWSERS_PATH"] if key in os.environ},
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool("hn_search", {"query": value})

    result = asyncio.run(invoke())
    assert not result.isError, result
    observed = json.loads(result.structuredContent["outputs"]["results"])
    assert observed["query"] == {"q": [value]}
    assert observed["segments"] == ["", "lookup", value, "details"]
    assert observed["host"] == urlsplit(echo_server).netloc
    assert result.structuredContent["outputs"]["fragment"] == "#fixed"
