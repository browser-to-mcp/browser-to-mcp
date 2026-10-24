"""Original single-page catalog with fixed answers; no public-site traffic or paging."""

import html
import json
import threading
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import yaml

from browser_to_mcp.loader import load_workflow

BOOKS = [
    ("blue", "Blue Notebook", "£12.50", "0", "0.0", "false", "PRIVATE-NOTE-BLUE"),
    ("green", "Green Notebook", "£0.00", "2", "12.5", "true", None),
    ("red", "Red Notebook", "£9.75", "3", "9.75", "false", "PRIVATE-NOTE-RED"),
]


def contract(base, *, allow_empty=False, kind="array"):
    safety = dict(
        timeout_seconds=1, risk="read_only", retry="none", idempotency="safe", confirmation="never"
    )
    fields = {
        "title": {"selector": "a.title", "source": "title"},
        "price_text": {"selector": ".price"},
        "product_url": {"selector": "a.title", "source": "href", "path_prefix": "/products/"},
        "count": {"selector": ".count"},
        "amount": {"selector": ".amount"},
        "available": {"selector": ".available"},
        "note": {"selector": ".note"},
    }
    properties = {name: {"type": "string"} for name in fields}
    properties.update(
        count={"type": "integer"},
        amount={"type": "number"},
        available={"type": "boolean"},
        note={"type": "string", "nullable": True, "sensitive": True},
    )
    output = dict(type=kind)
    if kind == "array":
        output.update(
            items={"type": "object", "properties": properties},
            max_items=10,
            allow_empty=allow_empty,
        )
    else:
        output["properties"] = properties
    record = dict(
        rows="article.product", fields=fields, duplicates="deduplicate", unique_by="product_url"
    )
    if allow_empty:
        record["empty_selector"] = ".empty"
    return dict(
        name="catalog_rows",
        description="Read fixed test products",
        version=1,
        risk="read_only",
        manifest={"version": 1},
        permissions={"allowed_domains": ["127.0.0.1"]},
        inputs={"case": {"type": "string", "required": True, "min_length": 1}},
        steps=[
            dict(id="open", action="goto", url=base + "/catalog?case={{case}}", **safety),
            dict(
                id="products",
                action="extract_records",
                selector="#results",
                record=record,
                **safety,
            ),
        ],
        outputs={"products": output},
        success=[
            dict(
                id="checked",
                field="products",
                assertion="output_matches_schema" if allow_empty else "output_not_empty",
            )
        ],
    )


def write_contract(path, data):
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return load_workflow(path)


def expected(base, order=(0, 1, 2)):
    return [
        dict(
            title=BOOKS[i][1],
            price_text=BOOKS[i][2],
            product_url=base + "/products/" + BOOKS[i][0] + "?edition=paper",
            count=int(BOOKS[i][3]),
            amount=float(BOOKS[i][4]),
            available=BOOKS[i][5] == "true",
            note=BOOKS[i][6],
        )
        for i in order
    ]


def page(case):
    rows = list(BOOKS)
    if case == "order":
        rows = [rows[1], rows[2], rows[0]]
    if case in {"duplicate", "conflict"}:
        rows += [rows[0] if case == "duplicate" else (*rows[0][:2], "£99.00", *rows[0][3:])]
    if case == "single":
        rows = rows[:1]
    if case in {"empty", "unmarked", "missing_area"}:
        rows = []
    if case == "too_many":
        rows *= 4
    if case == "byte_limit":
        rows = [(str(i), "x" * 4000, "£12.50", "0", "0", "false", "y" * 4000) for i in range(40)]
    cards = []
    for index, (slug, title, price, count, amount, available, note) in enumerate(rows):
        href = f"/products/{slug}?edition=paper#details"
        if case == "url_spaces":
            href = f"/products/{slug}?label=two  spaces&tag=C++#details"
        if case == "cart_link":
            href = "/cart?add-to-cart=123"
        if case == "outside_link":
            href = "https://outside.example/products/blue"
        if case == "secret_link":
            href = "/products/blue?token=PRIVATE-TOKEN"
        if case == "bad_number" and index == 1:
            amount = "$12.50"
        if case == "non_finite" and index == 1:
            amount = "NaN"
        if case == "huge_cell" and index == 1:
            title = "PRIVATE-LONG" * 500
        if case == "blank" and index == 1:
            title = "   "
        if case == "same_title" and index == 1:
            title = "Blue Notebook"
        if case == "unicode" and index == 0:
            title = "नमस्ते 日本語 café 🚀"
        if case == "credential" and index == 0:
            note = "Bearer NEVER-PRINT-THIS"
        cells = (
            f'<a class=title title=" {html.escape(title, quote=True)} " '
            f'href="{html.escape(href, quote=True)}">'
            f"{html.escape(title)}</a>"
            + ("" if case == "missing" and index == 1 else f"<span class=price>{price}</span>")
            + (f"<span class=price>{price}</span>" if case == "ambiguous" and index == 1 else "")
            + f"<span class=count>{count}</span><span class=amount>{amount}</span>"
            + f"<span class=available>{available}</span>"
            + (f"<span class=note>{note}</span>" if note is not None else "")
        )
        if case == "varying_classes":
            cells = cells.replace("class=title", f'class="title title-{slug}"')
            cells = cells.replace("class=price", f'class="price price-{slug}"')
        hidden = " hidden" if case == "hidden" and index == 1 else ""
        classes = f"product sku-{slug}" if case == "varying_classes" else "product"
        cards.append(f'<article class="{classes}" data-slug="{slug}"{hidden}>{cells}</article>')
    body = "".join(cards)
    if case == "nested":
        body = "<article class=product>" + body + "</article>"
    if case in {"empty", "mixed_empty"}:
        body += "<p class=empty>No products found</p>"
    area = "<section id=results class=results-area><h2>Products</h2>" + body + "</section>"
    if case == "missing_area":
        area = "<main>Generic verification page</main>"
    if case == "duplicate_area":
        area += area
    if case == "delay":
        script = (
            "setTimeout(()=>document.querySelector('#results').innerHTML="
            + json.dumps("<h2>Products</h2>" + body)
            + ",250)"
        )
        area = (
            "<section id=results class=results-area><h2>Loading</h2></section><script>"
            + script
            + "</script>"
        )
    return (
        "<!doctype html><meta charset=utf-8>"
        "<style>article{padding:8px} span{margin:4px}</style>"
        "<aside><a id=outside-title>Wrong Sidebar Product</a>"
        "<span class=price>£999.00</span></aside>" + area + "<a id=cart href=/cart>Add to cart</a>"
    )


class Site(ThreadingHTTPServer):
    def __init__(self, port=0):
        self.hits = Counter()
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlsplit(self.path)
        self.server.hits[parsed.path] += 1
        case = parse_qs(parsed.query).get("case", ["normal"])[0]
        body = page(case).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


@contextmanager
def serve():
    server = Site()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Original local record-output practice pages")
    parser.add_argument("--port", type=int, default=8766)
    server = Site(parser.parse_args().port)
    print(server.base + "/catalog?case=normal", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
