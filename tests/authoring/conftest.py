"""A deterministic local catalog. No third-party requests or accounts."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

import pytest

BOOKS = {
    "a-light-in-the-attic_1000": ("A Light in the Attic", "£51.77"),
    "shakespeares-sonnets_989": ("Shakespeare's Sonnets", "£20.66"),
    "space & hash# café": ("Special characters", "£12.34"),
}


@pytest.fixture(scope="module")
def catalog():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            slug = unquote(urlsplit(self.path).path.split("/")[2])
            book = BOOKS.get(slug)
            if slug == "delayed":
                book = BOOKS["a-light-in-the-attic_1000"]
            body = (
                "<!doctype html><meta charset=utf-8><aside><h1>Wrong book</h1>"
                "<p class=price_color>£99.99</p></aside>"
            )
            if book:
                content = (
                    f'<main class="product_main"><h1>{book[0]}</h1>'
                    f'<p class="price_color">{book[1]}</p>'
                    '<span id="count">0</span><span id="available">false</span></main>'
                )
                if slug == "delayed":
                    import json

                    body += (
                        "<script>setTimeout(()=>document.body.insertAdjacentHTML("
                        f"'beforeend', {json.dumps(content)}),250)</script>"
                    )
                else:
                    body += content
            elif slug == "broken":
                body += "<h2>Changed page</h2>"
            else:
                body += "<p>No book found</p>"
            data = body.encode()
            self.send_response(200 if book or slug == "broken" else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
