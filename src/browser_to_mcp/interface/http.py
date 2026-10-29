"""Loopback transport with per-process capabilities, exact origins and fixed assets."""

import hmac
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from browser_to_mcp.jobs.models import JobError
from browser_to_mcp.library.store import LibraryError

from .service import InterfaceError

MAX_BODY = 1_048_576
ASSETS = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, port=0):
        self.service = service
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.authority = f"127.0.0.1:{self.server_port}"
        self.origin = "http://" + self.authority
        self.url = self.origin + "/#" + self.token


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log capability URLs, input values or parser exceptions.

    def _reply(self, code, value, content_type="application/json"):
        data = (
            json.dumps(value, allow_nan=False).encode()
            if content_type == "application/json"
            else value
        )
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src "
            "'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _host(self):
        return self.headers.get_all("Host") == [self.server.authority]

    def do_GET(self):
        if not self._host() or self.headers.get("Origin") not in (None, self.server.origin):
            return self._reply(403, {"error": "Local access refused."})
        asset = ASSETS.get(self.path)
        if not asset:
            return self._reply(404, {"error": "Not found."})
        name, mime = asset
        data = files("browser_to_mcp.interface").joinpath("assets", name).read_bytes()
        self._reply(200, data, mime)

    def do_POST(self):
        if (
            not self._host()
            or self.path != "/api"
            or self.headers.get_all("Origin") != [self.server.origin]
            or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin")
            or not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + self.server.token
            )
        ):
            return self._reply(
                403, {"error": "Local access refused. Open the URL printed by this UI process."}
            )
        if self.headers.get("Content-Type") != "application/json" or self.headers.get(
            "Transfer-Encoding"
        ):
            return self._reply(415, {"error": "Use application/json without transfer encoding."})
        try:
            lengths = self.headers.get_all("Content-Length")
            if not lengths or len(lengths) != 1:
                raise ValueError
            length = int(lengths[0])
            if not 1 <= length <= MAX_BODY:
                return self._reply(413, {"error": "Request exceeds the 1 MiB limit."})
            self.connection.settimeout(5)
            payload = json.loads(
                self.rfile.read(length),
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if (
                not isinstance(payload, dict)
                or set(payload) != {"operation", "arguments"}
                or not isinstance(payload["arguments"], dict)
            ):
                raise ValueError
            result = self.server.service.dispatch(payload["operation"], payload["arguments"])
            self._reply(200, result)
        except (InterfaceError, LibraryError) as error:
            self._reply(409, {"error": str(error)})
        except JobError as error:
            self._reply(409, {"error": error.message})
        except Exception:
            self._reply(
                400,
                {"error": "Request refused. Check fields, types, IDs and review revision."},
            )

    def do_OPTIONS(self):
        self._reply(403, {"error": "Cross-origin access is disabled."})
