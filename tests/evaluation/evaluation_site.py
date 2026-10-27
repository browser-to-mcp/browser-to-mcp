"""Original loopback pages; answers below are written independently of this renderer."""

import html
import threading
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import yaml


@contextmanager
def serve():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.server.hits.append(self.path)
            query = parse_qs(urlsplit(self.path).query)
            search = query.get("q", [""])[0]
            color = query.get("color", ["all"])[0]
            page = int(query.get("page", ["1"])[0])
            if self.server.mode == "repeat":
                page = 1
            rows = [
                ("blue", "Blue Notebook", "£12.50"),
                ("green", "Green Notebook", "£0.00"),
                ("red", "Red Notebook", "£9.75"),
            ]
            if search == "missing":
                rows = []
            elif search == "red":
                rows = rows[2:]
            if color != "all":
                rows = [r for r in rows if r[0] == color]
            if search == "notebook" and color == "all":
                rows = rows[:2] if page == 1 else rows[1:]
            cells = []
            for slug, title, price in rows:
                if self.server.mode == "wrong_price":
                    price = "£99.99"
                cells.append(
                    f'<article class="product"><a class="title" title=" {title} " '
                    f'href="/products/{slug}">{title}</a><b class="price">{price}</b></article>'
                )
            content = "".join(cells) or '<p class="empty">No matches</p>'
            if search == "broken":
                content = content.replace('class="price"', 'class="new-price"')
            if search == "challenge":
                content = "<h1>Verify you are human</h1>"
            if search == "notebook" and color == "all" and page == 1:
                target = "/catalog?" + urlencode({"q": search, "color": color, "page": 2})
                nav = f'<a class="next" href="{html.escape(target)}">Next</a>'
            else:
                nav = '<p class="end">End of results</p>'
            status = 503 if search == "outage" else 200
            body = (
                '<!doctype html><meta charset="utf-8"><aside>£999 sidebar</aside>'
                '<main id="results">' + content + "</main>" + nav
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.base = f"http://127.0.0.1:{server.server_port}"
    server.mode, server.hits = "normal", []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def candidate(base):
    safety = dict(
        timeout_seconds=1, risk="read_only", retry="none", idempotency="safe", confirmation="never"
    )
    return dict(
        name="catalog_check",
        description="Read the local public catalog.",
        version=1,
        manifest={"version": 1},
        risk="read_only",
        permissions={"allowed_domains": ["127.0.0.1"]},
        inputs={k: {"type": "string", "min_length": 1} for k in ("query", "color")},
        steps=[
            dict(
                id="open",
                action="goto",
                url=base + "/catalog?q={{query}}&color={{color}}",
                **safety,
            ),
            dict(
                id="products",
                action="extract_records",
                selector="#results",
                **safety,
                record=dict(
                    rows="article.product",
                    empty_selector=".empty",
                    duplicates="deduplicate",
                    unique_by="product_url",
                    fields={
                        "title": {"selector": "a.title", "source": "title"},
                        "price_text": {"selector": ".price"},
                        "product_url": {
                            "selector": "a.title",
                            "source": "href",
                            "path_prefix": "/products/",
                        },
                    },
                ),
                paging=dict(
                    next_selector="a.next",
                    end_selector=".end",
                    page_parameter="page",
                    max_pages=2,
                    timeout_seconds=5,
                    status_output="collection",
                ),
            ),
        ],
        outputs={
            "products": dict(
                type="array",
                max_items=10,
                allow_empty=True,
                items={
                    "type": "object",
                    "properties": {
                        k: {"type": "string"} for k in ("title", "price_text", "product_url")
                    },
                },
            ),
            "collection": {
                "type": "object",
                "properties": {
                    "pages_read": {"type": "integer"},
                    "complete": {"type": "boolean"},
                    "stop_reason": {"type": "string"},
                },
            },
        },
        success=[dict(id="checked", field="products", assertion="output_matches_schema")],
    )


def answers(base, keys=("blue", "green", "red"), pages=2):
    # Do not derive expectations from server rows, extracted DOM, selectors or the builder.
    fixed = {
        "blue": {
            "title": "Blue Notebook",
            "price_text": "£12.50",
            "product_url": base + "/products/blue",
        },
        "green": {
            "title": "Green Notebook",
            "price_text": "£0.00",
            "product_url": base + "/products/green",
        },
        "red": {
            "title": "Red Notebook",
            "price_text": "£9.75",
            "product_url": base + "/products/red",
        },
    }
    return {
        "products": [fixed[k] for k in keys],
        "collection": {"pages_read": pages, "complete": True, "stop_reason": "end"},
    }


def suite(base):
    cases = []
    for name, tags, query, color, expected in (
        ("shown", ["demonstration", "multiple_pages"], "notebook", "all", answers(base)),
        ("query_changed", ["changed_inputs"], "red", "all", answers(base, ("red",), 1)),
        ("color_changed", ["changed_inputs"], "notebook", "blue", answers(base, ("blue",), 1)),
        ("both_changed", ["combined_filters"], "red", "red", answers(base, ("red",), 1)),
        ("empty", ["zero_results"], "missing", "all", answers(base, (), 1)),
    ):
        cases.append(
            dict(
                id=name,
                covers=tags,
                inputs={"query": query, "color": color},
                expected={"outcome": "outputs", "outputs": expected},
            )
        )
    cases += [
        dict(
            id="invalid",
            covers=["invalid_input"],
            inputs={"query": True, "color": "all"},
            expected={"outcome": "error", "category": "input", "path": "inputs"},
        ),
        dict(
            id="changed_markup",
            covers=["changed_page"],
            inputs={"query": "broken", "color": "all"},
            expected={
                "outcome": "error",
                "category": "assertion",
                "path": "outputs.products.0.price_text",
            },
        ),
    ]
    return dict(
        name="Catalog correctness",
        requirements_source="THREE_SITE_PILOT.md local cases",
        expectations_source="Independently fixed Blue/Green/Red values in this test plan",
        mode="controlled",
        cases=cases,
        timeout_seconds=10,
        wall_seconds=90,
    )


def write_candidate(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return path
