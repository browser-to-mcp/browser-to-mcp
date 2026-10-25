"""Original multi-page catalog and hostile variants, served only on loopback."""

import argparse
import html
import json
import threading
import time
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import yaml

from browser_to_mcp.loader import load_workflow

PRODUCTS = [
    ("blue", "Blue Notebook", "£12.50"),
    ("green", "Green Notebook", "£0.00"),
    ("red", "Red Notebook", "£9.75"),
]


def contract(base, *, layout="cards", style="query", max_pages=20, max_items=100):
    safety = dict(
        timeout_seconds=2, risk="read_only", retry="none", idempotency="safe", confirmation="never"
    )
    paging = dict(
        next_selector="a.next",
        end_selector=".end",
        max_pages=max_pages,
        timeout_seconds=20,
        status_output="collection",
    )
    paging.update(
        {"page_parameter": "page"} if style == "query" else {"page_path": "/catalog/page/{page}/"}
    )
    return dict(
        name="collect_catalog",
        description="Collect the original local practice catalog.",
        version=1,
        risk="read_only",
        manifest={"version": 1},
        permissions={"allowed_domains": ["127.0.0.1"]},
        inputs={"case": {"type": "string", "min_length": 1}},
        steps=[
            dict(
                id="open",
                action="goto",
                url=base + "/catalog?case={{case}}&layout=" + layout + "&style=" + style,
                **safety,
            ),
            dict(
                id="products",
                action="extract_records",
                selector="#results",
                **safety,
                record=dict(
                    rows=".product",
                    fields={
                        "title": {"selector": "a.title", "source": "title"},
                        "price_text": {"selector": ".price"},
                        "product_url": {
                            "selector": "a.title",
                            "source": "href",
                            "path_prefix": "/products/",
                        },
                    },
                    empty_selector=".empty",
                    duplicates="deduplicate",
                    unique_by="product_url",
                ),
                paging=paging,
            ),
        ],
        outputs={
            "products": dict(
                type="array",
                max_items=max_items,
                allow_empty=True,
                items={
                    "type": "object",
                    "properties": {
                        key: {"type": "string"} for key in ("title", "price_text", "product_url")
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


def write_contract(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return load_workflow(path)


def expected(base, order=(0, 1, 2)):
    return [
        dict(
            title=PRODUCTS[i][1],
            price_text=PRODUCTS[i][2],
            product_url=base + "/products/" + PRODUCTS[i][0],
        )
        for i in order
    ]


def markup(query, number, port):
    case = query.get("case", ["full"])[0]
    layout = query.get("layout", ["cards"])[0]
    style = query.get("style", ["query"])[0]
    order = [0, 1] if number == 1 else [1, 2]
    if case == "single":
        order = [0, 1, 2]
    if case == "empty" or case == "late_empty" and number == 2:
        order = []
    if case == "loop" and number > 1:
        order = [1, 0]
    if case == "within_duplicate":
        order += [order[0]]
    if case == "many":
        order = list(range(101))
    if case == "changing":
        order = [number + 2]
    body = []
    for i in order:
        slug, title, price = PRODUCTS[i] if i < 3 else (f"item-{i}", f"Item {i}", "£1.00")
        if case == "conflict" and number == 2 and i == 1:
            price = "£999.00"
        link = f'<a class="title" title="{title}" href="/products/{slug}">{title}</a>'
        cell = f'<span class="price">{price}</span>'
        if case == "typed":
            cell += '<span class="count">0</span><span class="amount">0.0</span>'
            cell += '<span class="available">false</span>'
        if case == "broken" and number == 2:
            cell = ""
        body.append(
            f'<tr class="product"><td>{link}</td><td>{cell}</td></tr>'
            if layout == "table"
            else f'<article class="product">{link}{cell}</article>'
        )
    content = "".join(body) or '<p class="empty">No products found.</p>'
    if layout == "table" and body:
        content = "<table><tbody>" + content + "</tbody></table>"
    terminal = number == 2 or case in {"single", "empty", "many"}
    if case in {"loop", "changing"}:
        terminal = False
    target_query = {key: values for key, values in query.items() if key != "page"}
    path = "/catalog"
    if style == "query":
        target_query["page"] = [str(number + 1)]
    else:
        path = f"/catalog/page/{number + 1}/"
    target = path + "?" + urlencode(target_query, doseq=True)
    if case == "stuck":
        target = "/catalog?" + urlencode(query, doseq=True)
    if case == "filters":
        target = target.replace("case=filters", "case=full")
    if case == "outside":
        target = f"http://localhost:{port}" + target
    if case == "port":
        target = "http://127.0.0.1:9" + target
    if case == "target":
        target = target.replace("/catalog", "/cart", 1)
    if case == "secret":
        target += "&access_token=PRIVATE-TOKEN"
    if case == "fragment":
        target += "#changed"
    if case == "skip":
        target = target.replace("page=2", "page=3")
    if case == "duplicate_page":
        target += "&page=2"
    next_link = f'<a class="next" href="{html.escape(target, quote=True)}">Next</a>'
    if case == "disabled":
        next_link = next_link.replace('class="next"', 'class="next" aria-disabled="true"')
    if case == "button":
        next_link = '<button class="next">Next</button>'
    nav = '<p class="end">End of results</p>' if terminal else next_link
    if case == "ambiguous":
        nav = next_link * 2
    if case == "mixed_end":
        nav = next_link + '<p class="end">End</p>'
    if case == "no_end" and number == 2:
        nav = ""
    if case == "challenge" and number == 2:
        content, nav = "<h1>Verify you are human</h1>", ""
    content = '<section id="results">' + content + "</section>" + nav
    if layout == "delay":
        content = (
            '<div id="mount"></div><script>setTimeout(() => {'
            + ('document.querySelector("#mount").innerHTML = ' + json.dumps(content))
            + ";}, 100);</script>"
        )
    return '<!doctype html><meta charset="utf-8"><aside>Cart total £100</aside>' + content


@contextmanager
def serve(port=0):
    hits = Counter()
    second_page = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            number = int(query.get("page", ["1"])[0])
            if parsed.path.startswith("/catalog/page/"):
                number = int(parsed.path.split("/")[3])
            case = query.get("case", ["full"])[0]
            hits[(case, number)] += 1
            if number == 2:
                second_page.set()
            if number == 2 and case == "slow":
                release.wait(10)
            status = 404 if case == "404" and number == 2 or case == "initial404" else 200
            if case == "redirect" and number == 2:
                self.send_response(302)
                self.send_header("Location", "/forbidden")
                self.end_headers()
                return
            if parsed.path == "/forbidden":
                hits["forbidden"] += 1
            body = markup(query, number, self.server.server_port).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.base = f"http://127.0.0.1:{server.server_port}"
    server.hits, server.second_page, server.release = hits, second_page, release
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    with serve(args.port) as site:
        print(site.base, flush=True)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
