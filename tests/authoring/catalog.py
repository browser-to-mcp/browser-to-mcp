"""Original local pilot pages, shared by browser tests and the manual walkthrough."""

from __future__ import annotations

import argparse
import html
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

BOOKS = {
    "a-light-in-the-attic_1000": ("A Light in the Attic", "£51.77"),
    "shakespeares-sonnets_989": ("Shakespeare's Sonnets", "£20.66"),
    "space & hash# café": ("Special characters", "£12.34"),
}
MODES = ("normal", "delayed", "broken", "challenge", "bad_count")


class Catalog:
    def __init__(self, mode: str = "normal"):
        self.mode = mode
        self.base_url = ""
        self.requests: list[str] = []
        self.lock = threading.Lock()

    def page(self, path: str) -> tuple[int, str]:
        parts = urlsplit(path).path.strip("/").split("/")
        slug = unquote(parts[1]) if len(parts) == 3 and parts[0] == "catalogue" else ""
        book = BOOKS.get(slug)
        if slug == "changed-layout":
            book = ("Changed layout", "£1.00")
        body = (
            '<!doctype html><html><head><meta charset="utf-8"><title>Local book catalog</title>'
            '</head><body><nav><a id="book-a" '
            'href="/catalogue/a-light-in-the-attic_1000/index.html">'
            'A Light in the Attic</a> <a id="book-b" '
            'href="/catalogue/shakespeares-sonnets_989/index.html">Shakespeare’s Sonnets</a></nav>'
            '<aside><h1>Wrong book</h1><p class="price_color">£99.99</p></aside>'
        )
        status = 200
        if self.mode == "challenge":
            body += "<p>This is a local challenge example. No product result is available.</p>"
        elif book:
            title = (
                ""
                if self.mode == "broken" or slug == "changed-layout"
                else f"<h1>{html.escape(book[0])}</h1>"
            )
            count = "unknown" if self.mode == "bad_count" else "0"
            content = (
                f'<main class="product_main">{title}'
                f'<p class="price_color">{book[1]}</p><span id="count">{count}</span>'
                '<span id="available">false</span><span id="amount">0.0</span></main>'
            )
            if self.mode == "delayed":
                body += (
                    "<script>setTimeout(()=>document.body.insertAdjacentHTML("
                    f"'beforeend', {json.dumps(content)}),250)</script>"
                )
            else:
                body += content
        elif path not in {"/", "/index.html"}:
            status = 404
            body += "<p>No book found</p>"
        return status, body + "</body></html>"


@contextmanager
def serve_catalog(*, port: int = 0, mode: str = "normal"):
    """Bind only loopback; always stop the server thread, including on test failures."""
    catalog = Catalog(mode)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with catalog.lock:
                catalog.requests.append(self.path)
            status, body = catalog.page(self.path)
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    catalog.base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield catalog
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("Local catalog did not stop within five seconds.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=MODES, default="normal")
    args = parser.parse_args()
    try:
        with serve_catalog(port=args.port, mode=args.mode) as catalog:
            print(f"Local catalog: {catalog.base_url} (Ctrl+C to stop)", flush=True)
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
