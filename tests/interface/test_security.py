import http.client
import json
import threading
from contextlib import contextmanager

import pytest

from browser_to_mcp.interface.http import LocalServer
from browser_to_mcp.interface.service import Interface


@contextmanager
def interface(tmp_path, *, worker=False):
    service = Interface(
        tmp_path / "ui", tmp_path / "jobs", tmp_path / "library", start_worker=worker
    )
    server = LocalServer(service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        service.close()


def request(server, payload=None, *, headers=None, path="/api", method="POST"):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    body = json.dumps(payload or {"operation": "status", "arguments": {}})
    values = {
        "Host": server.authority,
        "Origin": server.origin,
        "Authorization": "Bearer " + server.token,
        "Content-Type": "application/json",
    }
    if headers:
        values.update(headers)
    connection.request(method, path, body, values)
    response = connection.getresponse()
    status, data, result_headers = response.status, response.read(), dict(response.getheaders())
    connection.close()
    return status, data, result_headers


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://unrelated.example"},
        {"Origin": "null"},
        {"Origin": ""},
        {"Host": "evil.example"},
        {"Host": "localhost"},
        {"Authorization": ""},
        {"Authorization": "Bearer wrong"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_foreign_and_unauthenticated_calls_fail_closed(tmp_path, headers):
    with interface(tmp_path) as (_, server):
        assert request(server, headers=headers)[0] == 403


def test_fixed_assets_and_security_headers(tmp_path):
    with interface(tmp_path) as (_, server):
        code, body, headers = request(server, path="/", method="GET")
        assert code == 200 and b"Local tools" in body
        assert server.token.encode() not in body
        assert headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
        assert "Access-Control-Allow-Origin" not in headers
        assert request(server, path="/../service.py", method="GET")[0] == 404
        assert request(server, method="OPTIONS")[0] == 403
        assert request(server, headers={"Content-Type": "text/plain"})[0] == 415
        assert request(server)[0] == 200


def test_unknown_operation_and_invalid_json_return_safe_errors(tmp_path):
    with interface(tmp_path) as (_, server):
        code, body, _ = request(server, {"operation": "SECRET-OPERATION", "arguments": {}})
        assert code == 409
        assert b"SECRET-OPERATION" not in body
        code, body, _ = request(
            server, {"operation": "read", "arguments": {"job_id": "PRIVATE-PARSER"}}
        )
        assert code >= 400 and b"PRIVATE-PARSER" not in body


def test_interface_directory_has_one_owner(tmp_path):
    from browser_to_mcp.interface.service import InterfaceError

    with interface(tmp_path):
        with pytest.raises(InterfaceError, match="Another interface"):
            Interface(
                tmp_path / "ui",
                tmp_path / "other-jobs",
                tmp_path / "other-library",
                start_worker=False,
            )
