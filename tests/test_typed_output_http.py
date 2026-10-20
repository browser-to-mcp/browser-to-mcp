"""Check matching typed CLI, MCP STDIO, and trace results through real Chromium."""

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
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from browser_to_mcp.cli import run
from browser_to_mcp.compiler import compile_workflow
from browser_to_mcp.loader import load_workflow

SOURCE = Path(__file__).parents[1] / "workflows" / "hn_search.yaml"
PRIVATE = "private-output-value"
CASES = {
    "normal": (
        {
            "title": "  Copper kettle\nKeep this spacing  ",
            "stock_count": "12",
            "price": "19.99",
            "available": "true",
        },
        None,
    ),
    "zero": ({"stock_count": "0", "price": "0", "available": "false"}, None),
    "secret_echo": ({"title": "Visible " + PRIVATE}, None),
    "bad_count": ({"stock_count": PRIVATE}, "stock_count"),
    "bad_price": ({"price": "$19.99"}, "price"),
    "bad_boolean": ({"available": "available"}, "available"),
    "blank_title": ({"title": " \n\t"}, "title"),
    "non_finite": ({"price": "NaN"}, "price"),
    "overflow": ({"price": "1e9999"}, "price"),
}


def page_values(case: str) -> dict[str, str]:
    return {
        "title": "Copper kettle",
        "stock_count": "12",
        "price": "19.99",
        "available": "true",
        **CASES[case][0],
    }


class FieldHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        case = parse_qs(urlsplit(self.path).query).get("case", ["normal"])[0]
        fields = page_values(case)
        body = '<!doctype html><meta charset="utf-8">' + "".join(
            f'<pre id="{name}">{html.escape(value)}</pre>' for name, value in fields.items()
        )
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture(scope="module")
def fields_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FieldHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/?case={{{{case}}}}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def write_workflow(tmp_path: Path, url: str) -> Path:
    contract = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    contract["permissions"]["allowed_domains"] = ["127.0.0.1"]
    contract["inputs"] = {
        "case": {"type": "string"},
        "secret": {"type": "string", "sensitive": True},
        "public": {"type": "string"},
    }
    policy = {
        "timeout_seconds": 5,
        "risk": "read_only",
        "idempotency": "safe",
        "retry": "none",
        "confirmation": "never",
    }
    contract["outputs"] = {
        "title": {"type": "string"},
        "stock_count": {"type": "integer"},
        "price": {"type": "number"},
        "available": {"type": "boolean"},
    }
    contract["steps"] = [
        {"id": "open", "action": "goto", "url": url, **policy},
        *[
            {"id": name, "action": "extract_text", "selector": f"#{name}", **policy}
            for name in contract["outputs"]
        ],
    ]
    contract["success"] = [
        {"id": name + "_present", "assertion": "output_not_empty", "field": name}
        for name in contract["outputs"]
    ]
    path = tmp_path / "fields.yaml"
    path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    return path


async def invoke_stdio(workflow_dir: Path, inputs: dict[str, str]):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "from browser_to_mcp.cli import main; main()",
            "server",
            "--workflow-dir",
            str(workflow_dir),
            "--trace-dir",
            str(workflow_dir / "traces"),
        ],
        env={key: os.environ[key] for key in ["PLAYWRIGHT_BROWSERS_PATH"] if key in os.environ},
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool("hn_search", inputs)


@pytest.mark.parametrize("case", list(CASES))
def test_cli_mcp_and_trace_agree_on_typed_outputs(
    tmp_path: Path, fields_url: str, capsys, case: str
) -> None:
    path = write_workflow(tmp_path, fields_url)
    inputs = {"case": case, "secret": PRIVATE, "public": "echo " + PRIVATE}
    arguments = ["replay", str(path), "--trace-dir", str(tmp_path / "traces")]
    for name, value in inputs.items():
        arguments.extend(["--input", f"{name}={value}"])
    exit_code = run(arguments)
    captured = capsys.readouterr()
    cli = json.loads(captured.out if exit_code == 0 else captured.err)
    mcp = asyncio.run(invoke_stdio(tmp_path, inputs))
    expected_error = CASES[case][1]

    if expected_error:
        assert exit_code == 1
        assert captured.out == ""
        assert cli["status"] == "failed"
        assert mcp.isError
        for result in (cli, mcp.structuredContent):
            assert result["category"] == "assertion"
            assert result["path"] == f"outputs.{expected_error}"
            assert PRIVATE not in json.dumps(result)
            trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
            assert trace["status"] == "failed"
            assert trace["error"]["category"] == "assertion"
            assert trace["error"]["path"] == f"outputs.{expected_error}"
            assert len(trace["actions"]) == 5
            assert trace["screenshots"] == []
        assert cli["message"] == mcp.structuredContent["message"]
    else:
        assert exit_code == 0
        assert not mcp.isError, mcp
        raw = page_values(case)
        expected = {
            "title": raw["title"],
            "stock_count": int(raw["stock_count"]),
            "price": float(raw["price"]),
            "available": raw["available"] == "true",
        }
        compiled = compile_workflow(load_workflow(path))
        for result in (cli, mcp.structuredContent):
            assert result["outputs"] == expected
            candidate = {
                name: result[name]
                for name in (
                    "workflow",
                    "manifest_digest",
                    "outputs",
                    "trace_path",
                    "duration_seconds",
                )
            }
            compiled.output_model.model_validate(candidate)
            trace = json.loads(Path(result["trace_path"]).read_text(encoding="utf-8"))
            assert trace["status"] == "completed"
            assert trace["outputs"] == {
                **expected,
                "title": expected["title"].replace(PRIVATE, "[REDACTED]"),
            }
            assert all(assertion["status"] == "passed" for assertion in trace["assertions"])
            for name, value in expected.items():
                assert type(result["outputs"][name]) is type(value)
                assert type(trace["outputs"][name]) is type(value)

    for trace_path in (tmp_path / "traces").glob("*.json"):
        text = trace_path.read_text(encoding="utf-8")
        assert PRIVATE not in text
        trace = json.loads(text)
        assert trace["inputs"]["secret"] == "[REDACTED]"
        assert trace["inputs"]["public"] == "echo [REDACTED]"
    assert list((tmp_path / "traces").glob("*.png")) == []
