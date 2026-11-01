# Collect a catalog

`collect_catalog` lets your assistant choose an approved catalog and collect its
product names, displayed prices and links. Choose `books`, `laptops` or `clothing`;
only catalogs configured by the server owner are available.

After testing and publishing each workflow, start the library server:

```bash
browser-to-mcp library --directory ./library server \
  --catalog books=travel_books --catalog laptops=laptop_list
```

Replace the workflow names with your published names. Each needs a fixed starting
page. Paged workflows take only optional `max_pages` (1–20, default 20);
reviewed single-page workflows take no inputs. Both return a product list
with `title`, `price_text`, `product_url`, and, for paged workflows, its paging status. Rows must use
normalized text, reviewed product links and deduplication by product URL.
An existing search workflow needs a separately tested and approved fixed-page
version; the server never edits or publishes it for you.

Call `collect_catalog` with `{"catalog":"books","max_pages":1}`. The result contains
`catalog`, `items`, `pages_read`, `complete`, and `stop_reason` (`end` or
`page_limit`). Omitting `max_pages` allows up to 20 pages. A failure returns its
reason and trace, never a partly successful list. MCP metadata retains the exact
published version and trace. Restart the server after changing published versions.

The [travel-book example](../examples/catalog/books.yaml) reads one reviewed page.
After [testing and publishing it](DRAFT_CHECKS.md), use `--catalog books=travel_books`.
Every valid page limit returns that same complete page. A newly appearing Next
link causes a refusal until the workflow is reviewed again.
See the [dated live checks](evidence/shared-books-2026-11-01/README.md).

The [laptop example](../examples/catalog/laptops.yaml) uses TestingURL's reviewed
15-product practice page. After testing and publishing, use
`--catalog laptops=practice_laptops`. Its prices exclude the neighboring star rating.
See the [live evidence and site choice](evidence/shared-laptops-2026-11-01/README.md).

The original Web Scraper laptop site and the clothing site remain blocked.
Local paging checks still cover all three layouts.
