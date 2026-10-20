"""Pilot fixture routes, diagnostic modes, and teardown."""

import socket
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
from catalog import BOOKS, Catalog, serve_catalog


def test_original_fixture_covers_pilot_cases_and_modes():
    catalog = Catalog()
    status, body = catalog.page("/")
    assert status == 200 and 'id="book-a"' in body and 'id="book-b"' in body
    for slug, (title, price) in BOOKS.items():
        status, body = catalog.page(f"/catalogue/{slug}/index.html")
        if "#" not in slug:  # URL-encoded special characters are covered by the real journey.
            assert status == 200 and price in body
    assert catalog.page("/catalogue/browser-to-mcp-missing_0/index.html")[0] == 404
    assert catalog.page("/favicon.ico")[0] == 404
    catalog.mode = "broken"
    body = catalog.page("/catalogue/a-light-in-the-attic_1000/index.html")[1]
    assert "A Light in the Attic</h1>" not in body and 'class="product_main"' in body
    catalog.mode = "challenge"
    assert 'class="product_main"' not in catalog.page("/")[1]


def test_local_server_stops_even_when_consumer_fails():
    with pytest.raises(RuntimeError, match="consumer failed"):
        with serve_catalog() as catalog:
            address = urlsplit(catalog.base_url)
            with urlopen(catalog.base_url, timeout=5) as response:
                assert response.status == 200
            raise RuntimeError("consumer failed")
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", address.port), timeout=1)
