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
page, only the optional `max_pages` input (1–20, default 20), a paged product list
with `title`, `price_text`, `product_url`, and its paging status. Rows must use
normalized text, reviewed product links and deduplication by product URL.
An existing search workflow needs a separately tested and approved fixed-page
version; the server never edits or publishes it for you.

Call `collect_catalog` with `{"catalog":"books","max_pages":1}`. The result contains
`catalog`, `items`, `pages_read`, `complete`, and `stop_reason` (`end` or
`page_limit`). Omitting `max_pages` allows up to 20 pages. A failure returns its
reason and trace, never a partly successful list. MCP metadata retains the exact
published version and trace. Restart the server after changing published versions.

Local checks cover three layouts. The live laptop and clothing sites remain
blocked under the current access rules; this interface does not make them supported.
