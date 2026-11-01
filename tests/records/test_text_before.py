"""Reviewed literal separators isolate a price from neighboring rating text."""

import asyncio
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from browser_to_mcp.loader import load_workflow
from browser_to_mcp.models import RecordExtractionField
from browser_to_mcp.replay import WorkflowExecutionError, replay_workflow


@pytest.mark.parametrize(
    "text,valid",
    [
        ("£12.50 · ★★★☆☆", True),
        ("£0.00 · ★☆☆☆☆", True),
        ("£12.50", False),
        ("£12.50 · sale · ★★★☆☆", False),
        (" · ★★★☆☆", False),
        ("X" * 5000 + " · ★★★☆☆", False),
    ],
)
def test_literal_separator_requires_one_match_and_valid_required_value(tmp_path, text, valid):
    data = yaml.safe_load(Path("examples/catalog/books.yaml").read_text())
    data["steps"][1]["record"]["fields"]["price_text"]["text_before"] = " · "
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text(yaml.safe_dump(data))
    loaded = load_workflow(candidate)
    html = (
        '<main class="col-sm-8 col-md-9"><article class="product_pod">'
        '<h3><a title="Book" href="/catalogue/book/index.html">Book</a></h3>'
        f'<p class="price_color">{text}</p></article></main>'
    )
    if valid:
        result = asyncio.run(replay_workflow(loaded, [], evaluation_html=html))
        assert result.outputs["result"][0]["price_text"] == text.split(" · ")[0]
    else:
        with pytest.raises(WorkflowExecutionError) as error:
            asyncio.run(replay_workflow(loaded, [], evaluation_html=html))
        assert error.value.category == "assertion"


@pytest.mark.parametrize("source", ["href", "title"])
def test_separator_is_only_for_visible_text(source):
    with pytest.raises(ValidationError, match="text_before"):
        RecordExtractionField(
            selector="a",
            source=source,
            text_before=" · ",
            **({"path_prefix": "/products/"} if source == "href" else {}),
        )


def test_existing_workflow_identity_stays_pinned():
    assert load_workflow(Path("examples/catalog/books.yaml")).manifest_digest == (
        "sha256:5f42334f099f381d28892004804827b9b1eb4db1e847b766e66ad96c966725ec"
    )
    assert "text_before" not in RecordExtractionField(selector="p").model_dump()


@pytest.mark.parametrize("change", ["none", "rating_changed", "new_page", "cart", "foreign"])
def test_laptop_example_keeps_price_and_link_boundaries(tmp_path, change):
    data = yaml.safe_load(Path("examples/catalog/laptops.yaml").read_text())
    data["steps"][1]["timeout_seconds"] = 1
    candidate = tmp_path / "laptops.yaml"
    candidate.write_text(yaml.safe_dump(data))
    url = {"cart": "/cart?add=1", "foreign": "https://example.org/product/1"}.get(
        change, "/scraping/ecommerce/product/1"
    )
    price = "$86.00 / rating 2" if change == "rating_changed" else "$86.00 · ★★☆☆☆"
    next_link = '<a rel="next" href="?page=2">Next</a>' if change == "new_page" else ""
    html = (
        f'<main class="site-main"><div class="grid"><a class="card" href="{url}">'
        f"<h3>Vertex Air Laptop 107</h3><p>{price}</p></a></div>{next_link}</main>"
    )
    loaded = load_workflow(candidate)
    if change == "none":
        result = asyncio.run(replay_workflow(loaded, [], evaluation_html=html))
        assert result.outputs["result"] == [
            dict(
                title="Vertex Air Laptop 107",
                price_text="$86.00",
                product_url="https://testingurl.dev/scraping/ecommerce/product/1",
            )
        ]
    else:
        with pytest.raises(WorkflowExecutionError):
            asyncio.run(replay_workflow(loaded, [], evaluation_html=html))
