import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


@contextmanager
def pilot():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            query = parse_qs(urlsplit(self.path).query).get("q", ["one"])[0]
            title, price = (
                ("A Light in the Attic", "£51.77")
                if query == "one"
                else ("Shakespeare's Sonnets", "£20.66")
            )
            body = f"<main><article><h2>{title}</h2><b>{price}</b></article></main>"
            if query == "broken":
                body = body.replace("<b>", "<i>").replace("</b>", "</i>")
            data = ("<!doctype html><meta charset=utf-8>" + body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/?q=one"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def plan():
    return dict(
        schema_version=1,
        name="Book lookup checks",
        requirements_source="Pilot brief",
        expectations_source="Independently specified book titles and prices",
        mode="controlled",
        not_applicable={
            "combined_filters": "One input",
            "zero_results": "One required object",
            "multiple_pages": "One page",
        },
        cases=[
            dict(
                id="one",
                covers=["demonstration"],
                inputs={"query": "one"},
                expected={
                    "outcome": "outputs",
                    "outputs": {"result": {"title": "A Light in the Attic", "price": "£51.77"}},
                },
            ),
            dict(
                id="two",
                covers=["changed_inputs"],
                inputs={"query": "two"},
                expected={
                    "outcome": "outputs",
                    "outputs": {"result": {"title": "Shakespeare's Sonnets", "price": "£20.66"}},
                },
            ),
            dict(
                id="invalid",
                covers=["invalid_input"],
                inputs={},
                expected={"outcome": "error", "category": "input", "path": "inputs"},
            ),
            dict(
                id="broken",
                covers=["changed_page"],
                inputs={"query": "broken"},
                expected={
                    "outcome": "error",
                    "category": "assertion",
                    "path": "outputs.result.price",
                },
            ),
        ],
    )


PROPOSAL = dict(
    status="draft",
    area="main",
    bindings={"query": {"location": "query", "key": "q"}},
    record={"rows": "article", "fields": {"title": {"selector": "h2"}, "price": {"selector": "b"}}},
)
